#include "can_rpc.h"

#include <ctype.h>
#include <stdio.h>
#include <string.h>

#include "esp_log.h"
#include "esp_mac.h"
#include "esp_timer.h"

#include "can_iface.h"
#include "commands.h"

#define TAG "CAN_RPC"

#define CAN_RPC_PROTO 0x01

typedef enum {
    CAN_RPC_TYPE_PING = 0x1,
    CAN_RPC_TYPE_PONG = 0x2,
    CAN_RPC_TYPE_CMD = 0x3,
    CAN_RPC_TYPE_RESP = 0x4,
} can_rpc_type_t;

// Hard caps to keep implementation small and deterministic.
#define CAN_RPC_MAX_CMD_CHUNKS 64
#define CAN_RPC_CMD_CHUNK_BYTES 4
#define CAN_RPC_MAX_CMD_LEN (CAN_RPC_MAX_CMD_CHUNKS * CAN_RPC_CMD_CHUNK_BYTES)

#define CAN_RPC_MAX_RESP_CHUNKS 64
#define CAN_RPC_RESP_CHUNK_BYTES 3
#define CAN_RPC_MAX_RESP_LEN (CAN_RPC_MAX_RESP_CHUNKS * CAN_RPC_RESP_CHUNK_BYTES)

#define CAN_RPC_NEIGHBORS_MAX 16
#define CAN_RPC_NEIGHBOR_TIMEOUT_MS (30 * 1000)

typedef struct {
    bool in_use;
    uint8_t node_id;
    uint8_t mac[6];
    int64_t last_seen_ms;
} neighbor_t;

static neighbor_t s_neighbors[CAN_RPC_NEIGHBORS_MAX];

static uint8_t s_node_id = 1;
static uint8_t s_seq4 = 0;
static uint8_t s_seq8 = 0;

static uint32_t fnv1a32(const uint8_t *data, size_t len)
{
    uint32_t h = 2166136261u;
    for (size_t i = 0; i < len; i++) {
        h ^= (uint32_t)data[i];
        h *= 16777619u;
    }
    return h;
}

static uint8_t node_id_from_unique_id(void)
{
    // Use the chip-unique factory-programmed base MAC in EFUSE as a stable unique identifier.
    uint8_t mac[6] = {0};
    (void)esp_efuse_mac_get_default(mac);

    // Hash into a pseudo-random but stable value, then map to 1..254.
    uint32_t h = fnv1a32(mac, sizeof(mac));
    h ^= (h >> 16);
    uint8_t id = (uint8_t)((h % 254u) + 1u);
    if (id == 0 || id == CAN_RPC_BROADCAST_ID) {
        id = 1;
    }
    return id;
}

static uint32_t can_rpc_make_id(can_rpc_type_t type, uint8_t dst, uint8_t src, uint8_t seq4)
{
    // 29-bit extended ID:
    // [28:24]=proto(5) [23:20]=type(4) [19:12]=dst(8) [11:4]=src(8) [3:0]=seq4(4)
    return ((uint32_t)(CAN_RPC_PROTO & 0x1F) << 24) | ((uint32_t)(type & 0x0F) << 20) | ((uint32_t)dst << 12) |
           ((uint32_t)src << 4) | (uint32_t)(seq4 & 0x0F);
}

static bool can_rpc_parse_id(uint32_t id, can_rpc_type_t *out_type, uint8_t *out_dst, uint8_t *out_src, uint8_t *out_seq4)
{
    const uint8_t proto = (uint8_t)((id >> 24) & 0x1F);
    if (proto != CAN_RPC_PROTO) {
        return false;
    }

    if (out_type) {
        *out_type = (can_rpc_type_t)((id >> 20) & 0x0F);
    }
    if (out_dst) {
        *out_dst = (uint8_t)((id >> 12) & 0xFF);
    }
    if (out_src) {
        *out_src = (uint8_t)((id >> 4) & 0xFF);
    }
    if (out_seq4) {
        *out_seq4 = (uint8_t)(id & 0x0F);
    }
    return true;
}

bool can_rpc_is_id(uint32_t id)
{
    return can_rpc_parse_id(id, NULL, NULL, NULL, NULL);
}

static int64_t now_ms(void)
{
    return esp_timer_get_time() / 1000;
}

static void neighbor_upsert(uint8_t node_id, const uint8_t mac[6])
{
    if (node_id == 0 || node_id == CAN_RPC_BROADCAST_ID) {
        return;
    }

    // Update existing
    for (int i = 0; i < CAN_RPC_NEIGHBORS_MAX; i++) {
        if (s_neighbors[i].in_use && s_neighbors[i].node_id == node_id) {
            memcpy(s_neighbors[i].mac, mac, 6);
            s_neighbors[i].last_seen_ms = now_ms();
            return;
        }
    }

    // Insert into free slot
    for (int i = 0; i < CAN_RPC_NEIGHBORS_MAX; i++) {
        if (!s_neighbors[i].in_use) {
            s_neighbors[i].in_use = true;
            s_neighbors[i].node_id = node_id;
            memcpy(s_neighbors[i].mac, mac, 6);
            s_neighbors[i].last_seen_ms = now_ms();
            return;
        }
    }

    // Evict oldest
    int oldest = 0;
    for (int i = 1; i < CAN_RPC_NEIGHBORS_MAX; i++) {
        if (s_neighbors[i].last_seen_ms < s_neighbors[oldest].last_seen_ms) {
            oldest = i;
        }
    }
    s_neighbors[oldest].in_use = true;
    s_neighbors[oldest].node_id = node_id;
    memcpy(s_neighbors[oldest].mac, mac, 6);
    s_neighbors[oldest].last_seen_ms = now_ms();
}

static void neighbors_prune(void)
{
    const int64_t cutoff = now_ms() - CAN_RPC_NEIGHBOR_TIMEOUT_MS;
    for (int i = 0; i < CAN_RPC_NEIGHBORS_MAX; i++) {
        if (s_neighbors[i].in_use && s_neighbors[i].last_seen_ms < cutoff) {
            s_neighbors[i].in_use = false;
        }
    }
}

size_t can_rpc_get_neighbors(can_rpc_neighbor_info_t *out, size_t max_out)
{
    neighbors_prune();

    size_t total = 0;
    const int64_t now = now_ms();

    for (int i = 0; i < CAN_RPC_NEIGHBORS_MAX; i++) {
        if (!s_neighbors[i].in_use) {
            continue;
        }
        total++;
        if (out != NULL && max_out > 0) {
            const size_t idx = total - 1;
            if (idx < max_out) {
                out[idx].node_id = s_neighbors[i].node_id;
                memcpy(out[idx].mac, s_neighbors[i].mac, 6);
                int64_t age = now - s_neighbors[i].last_seen_ms;
                if (age < 0) {
                    age = 0;
                }
                if (age > INT32_MAX) {
                    age = INT32_MAX;
                }
                out[idx].age_ms = (uint32_t)age;
            }
        }
    }

    if (out == NULL) {
        return total;
    }
    if (total > max_out) {
        return max_out;
    }
    return total;
}

void can_rpc_init(void)
{
    s_node_id = node_id_from_unique_id();

    memset(s_neighbors, 0, sizeof(s_neighbors));

    can_iface_set_rx_handler(can_rpc_on_frame);

    uint8_t mac[6] = {0};
    (void)esp_efuse_mac_get_default(mac);
    ESP_LOGI(TAG, "Node ID=%u (default from eFuse MAC %02X:%02X:%02X:%02X:%02X:%02X)", (unsigned)s_node_id, (unsigned)mac[0],
             (unsigned)mac[1], (unsigned)mac[2], (unsigned)mac[3], (unsigned)mac[4], (unsigned)mac[5]);
}

uint8_t can_rpc_get_node_id(void)
{
    return s_node_id;
}

bool can_rpc_set_node_id(uint8_t node_id)
{
    if (node_id == 0 || node_id == CAN_RPC_BROADCAST_ID) {
        return false;
    }
    s_node_id = node_id;
    return true;
}

void can_rpc_ping_all(void)
{
    uint8_t data[8] = {0};
    data[0] = s_seq8++;

    const uint32_t id = can_rpc_make_id(CAN_RPC_TYPE_PING, CAN_RPC_BROADCAST_ID, s_node_id, s_seq4++);
    (void)can_iface_send(id, true, data, sizeof(data));
}

static void can_rpc_send_pong(uint8_t ping_seq8)
{
    uint8_t mac[6] = {0};
    (void)esp_read_mac(mac, ESP_MAC_WIFI_STA);

    uint8_t data[8] = {0};
    data[0] = ping_seq8;
    memcpy(&data[1], mac, 6);
    data[7] = 1; // protocol/info version for this PONG payload

    const uint32_t id = can_rpc_make_id(CAN_RPC_TYPE_PONG, CAN_RPC_BROADCAST_ID, s_node_id, s_seq4++);
    (void)can_iface_send(id, true, data, sizeof(data));
}

typedef struct {
    char buf[256];
    size_t len;
} reply_buf_t;

static void reply_buf_append(reply_buf_t *rb, const char *s)
{
    if (rb == NULL || s == NULL) {
        return;
    }
    size_t sl = strlen(s);
    size_t space = sizeof(rb->buf) - 1 - rb->len;
    if (space == 0) {
        return;
    }
    if (sl > space) {
        sl = space;
    }
    memcpy(&rb->buf[rb->len], s, sl);
    rb->len += sl;
    rb->buf[rb->len] = 0;
}

static reply_buf_t *s_active_reply;

static void reply_cb_collect_active(const char *s)
{
    reply_buf_append(s_active_reply, s);
}

static bool looks_err_prefix(const char *s)
{
    return (s != NULL) && (strncmp(s, "ERR", 3) == 0);
}

static void can_rpc_send_resp(uint8_t dst_node_id, uint8_t seq8, uint8_t status, const char *text)
{
    if (dst_node_id == 0) {
        return;
    }

    if (text == NULL) {
        text = "";
    }

    const size_t text_len = strlen(text);
    const size_t total_chunks = (text_len == 0) ? 1 : (size_t)((text_len + CAN_RPC_RESP_CHUNK_BYTES - 1) / CAN_RPC_RESP_CHUNK_BYTES);
    if (total_chunks > CAN_RPC_MAX_RESP_CHUNKS) {
        // Truncate by limiting chunk count.
        // Still send something so the caller gets a response.
    }

    size_t chunks_to_send = total_chunks;
    if (chunks_to_send > CAN_RPC_MAX_RESP_CHUNKS) {
        chunks_to_send = CAN_RPC_MAX_RESP_CHUNKS;
    }

    for (size_t i = 0; i < chunks_to_send; i++) {
        uint8_t data[8] = {0};
        data[0] = (uint8_t)i;                 // chunk idx
        data[1] = (uint8_t)chunks_to_send;    // total chunks
        data[2] = 0;                          // chunk len (0..3)
        data[3] = seq8;                       // seq8
        data[4] = status;                     // status

        size_t off = i * CAN_RPC_RESP_CHUNK_BYTES;
        size_t remain = (text_len > off) ? (text_len - off) : 0;
        size_t take = remain;
        if (take > CAN_RPC_RESP_CHUNK_BYTES) {
            take = CAN_RPC_RESP_CHUNK_BYTES;
        }
        data[2] = (uint8_t)take;
        if (take > 0) {
            memcpy(&data[5], text + off, take);
        }

        const uint32_t id = can_rpc_make_id(CAN_RPC_TYPE_RESP, dst_node_id, s_node_id, s_seq4++);
        (void)can_iface_send(id, true, data, sizeof(data));
    }
}

static bool can_rpc_send_cmd_frames(uint8_t dst_node_id, uint8_t seq8, const char *cmd)
{
    if (cmd == NULL) {
        return false;
    }

    size_t cmd_len = strlen(cmd);
    if (cmd_len == 0) {
        return false;
    }

    if (cmd_len > CAN_RPC_MAX_CMD_LEN) {
        return false;
    }

    size_t total_chunks = (cmd_len + CAN_RPC_CMD_CHUNK_BYTES - 1) / CAN_RPC_CMD_CHUNK_BYTES;
    if (total_chunks == 0 || total_chunks > CAN_RPC_MAX_CMD_CHUNKS) {
        return false;
    }

    for (size_t i = 0; i < total_chunks; i++) {
        uint8_t data[8] = {0};
        data[0] = (uint8_t)i;
        data[1] = (uint8_t)total_chunks;
        data[2] = 0; // chunk len (1..4)
        data[3] = seq8;

        size_t off = i * CAN_RPC_CMD_CHUNK_BYTES;
        size_t remain = cmd_len - off;
        size_t take = remain;
        if (take > CAN_RPC_CMD_CHUNK_BYTES) {
            take = CAN_RPC_CMD_CHUNK_BYTES;
        }
        data[2] = (uint8_t)take;
        memcpy(&data[4], cmd + off, take);

        const uint32_t id = can_rpc_make_id(CAN_RPC_TYPE_CMD, dst_node_id, s_node_id, s_seq4++);
        if (!can_iface_send(id, true, data, sizeof(data))) {
            return false;
        }
    }

    return true;
}

bool can_rpc_send_cmd_broadcast(const char *cmd)
{
    // Broadcast commands are executed by all nodes that receive them.
    // To avoid a response storm, receivers do not send RESP for broadcast CMD.
    const uint8_t seq8 = s_seq8++;
    return can_rpc_send_cmd_frames(CAN_RPC_BROADCAST_ID, seq8, cmd);
}

bool can_rpc_send_cmd(uint8_t dst_node_id, const char *cmd)
{
    if (dst_node_id == 0 || dst_node_id == CAN_RPC_BROADCAST_ID) {
        return false;
    }

    // If addressed to self, execute locally (no CAN traffic needed).
    if (dst_node_id == s_node_id) {
        char tmp[CAN_RPC_MAX_CMD_LEN + 1];
        size_t n = strlen(cmd);
        if (n > CAN_RPC_MAX_CMD_LEN) {
            return false;
        }
        memcpy(tmp, cmd, n);
        tmp[n] = 0;

        reply_buf_t rb = {.buf = {0}, .len = 0};
        s_active_reply = &rb;
        commands_handle_line(tmp, reply_cb_collect_active);
        s_active_reply = NULL;
        return true;
    }

    const uint8_t seq8 = s_seq8++;
    return can_rpc_send_cmd_frames(dst_node_id, seq8, cmd);
}

typedef struct {
    bool active;
    uint8_t src;
    uint8_t dst;
    uint8_t seq8;
    uint8_t total_chunks;
    uint8_t received[CAN_RPC_MAX_CMD_CHUNKS];
    char cmd[CAN_RPC_MAX_CMD_LEN + 1];
} cmd_asm_t;

typedef struct {
    bool active;
    uint8_t src;
    uint8_t dst;
    uint8_t seq8;
    uint8_t status;
    uint8_t total_chunks;
    uint8_t received[CAN_RPC_MAX_RESP_CHUNKS];
    char resp[CAN_RPC_MAX_RESP_LEN + 1];
} resp_asm_t;

static cmd_asm_t s_cmd_asm;
static resp_asm_t s_resp_asm;

static void cmd_asm_reset(void)
{
    memset(&s_cmd_asm, 0, sizeof(s_cmd_asm));
}

static void resp_asm_reset(void)
{
    memset(&s_resp_asm, 0, sizeof(s_resp_asm));
}

static void handle_ping(uint8_t src, const uint8_t data[8], uint8_t dlc)
{
    (void)dlc;
    if (src == 0 || src == CAN_RPC_BROADCAST_ID) {
        return;
    }

    const uint8_t seq8 = data[0];

    // Ignore our own broadcast ping.
    if (src == s_node_id) {
        return;
    }

    can_rpc_send_pong(seq8);
}

static void handle_pong(uint8_t src, const uint8_t data[8], uint8_t dlc)
{
    if (dlc < 8) {
        return;
    }

    if (src == 0 || src == CAN_RPC_BROADCAST_ID) {
        return;
    }

    // Ignore our own pong.
    if (src == s_node_id) {
        return;
    }

    uint8_t mac[6];
    memcpy(mac, &data[1], 6);

    neighbor_upsert(src, mac);
    neighbors_prune();

    ESP_LOGI(TAG, "NEIGHBOR id=%u mac=%02X:%02X:%02X:%02X:%02X:%02X", (unsigned)src, (unsigned)mac[0], (unsigned)mac[1],
             (unsigned)mac[2], (unsigned)mac[3], (unsigned)mac[4], (unsigned)mac[5]);
}

static void handle_cmd(uint8_t dst, uint8_t src, const uint8_t data[8], uint8_t dlc)
{
    if (dlc < 8) {
        return;
    }

    const bool is_broadcast = (dst == CAN_RPC_BROADCAST_ID);
    if (!is_broadcast && dst != s_node_id) {
        return;
    }

    const uint8_t chunk_idx = data[0];
    const uint8_t total = data[1];
    const uint8_t chunk_len = data[2];
    const uint8_t seq8 = data[3];

    if (src == 0 || src == CAN_RPC_BROADCAST_ID) {
        return;
    }

    // Ignore our own broadcast command frames.
    if (is_broadcast && src == s_node_id) {
        return;
    }

    if (total == 0 || total > CAN_RPC_MAX_CMD_CHUNKS) {
        // Reply with an error.
        can_rpc_send_resp(src, seq8, 1, "ERR cmd too long\n");
        return;
    }

    if (chunk_idx >= total) {
        return;
    }
    if (chunk_len > CAN_RPC_CMD_CHUNK_BYTES) {
        return;
    }

    // Start / reset assembly if new sequence
    if (!s_cmd_asm.active || s_cmd_asm.src != src || s_cmd_asm.dst != dst || s_cmd_asm.seq8 != seq8 || s_cmd_asm.total_chunks != total) {
        cmd_asm_reset();
        s_cmd_asm.active = true;
        s_cmd_asm.src = src;
        s_cmd_asm.dst = dst;
        s_cmd_asm.seq8 = seq8;
        s_cmd_asm.total_chunks = total;
    }

    if (chunk_idx >= CAN_RPC_MAX_CMD_CHUNKS) {
        return;
    }

    // Copy chunk into buffer
    const size_t off = (size_t)chunk_idx * CAN_RPC_CMD_CHUNK_BYTES;
    if (off + chunk_len > CAN_RPC_MAX_CMD_LEN) {
        return;
    }
    memcpy(&s_cmd_asm.cmd[off], &data[4], chunk_len);
    s_cmd_asm.received[chunk_idx] = 1;

    // Check completion
    for (uint8_t i = 0; i < total; i++) {
        if (s_cmd_asm.received[i] == 0) {
            return;
        }
    }

    // Determine actual string length (trim trailing NULs/spaces)
    size_t raw_len = (size_t)total * CAN_RPC_CMD_CHUNK_BYTES;
    if (raw_len > CAN_RPC_MAX_CMD_LEN) {
        raw_len = CAN_RPC_MAX_CMD_LEN;
    }
    while (raw_len > 0 && (s_cmd_asm.cmd[raw_len - 1] == 0)) {
        raw_len--;
    }

    s_cmd_asm.cmd[raw_len] = 0;

    // Execute
    reply_buf_t rb = {.buf = {0}, .len = 0};
    s_active_reply = &rb;
    commands_handle_line(s_cmd_asm.cmd, reply_cb_collect_active);
    s_active_reply = NULL;

    if (!is_broadcast) {
        const uint8_t status = looks_err_prefix(rb.buf) ? 1 : 0;
        can_rpc_send_resp(src, seq8, status, rb.buf);
    }

    cmd_asm_reset();
}

static void handle_resp(uint8_t dst, uint8_t src, const uint8_t data[8], uint8_t dlc)
{
    if (dlc < 8) {
        return;
    }

    if (dst != s_node_id) {
        return;
    }

    const uint8_t chunk_idx = data[0];
    const uint8_t total = data[1];
    const uint8_t chunk_len = data[2];
    const uint8_t seq8 = data[3];
    const uint8_t status = data[4];

    if (total == 0 || total > CAN_RPC_MAX_RESP_CHUNKS) {
        return;
    }
    if (chunk_idx >= total) {
        return;
    }
    if (chunk_len > CAN_RPC_RESP_CHUNK_BYTES) {
        return;
    }

    if (!s_resp_asm.active || s_resp_asm.src != src || s_resp_asm.dst != dst || s_resp_asm.seq8 != seq8 || s_resp_asm.total_chunks != total) {
        resp_asm_reset();
        s_resp_asm.active = true;
        s_resp_asm.src = src;
        s_resp_asm.dst = dst;
        s_resp_asm.seq8 = seq8;
        s_resp_asm.status = status;
        s_resp_asm.total_chunks = total;
    }

    const size_t off = (size_t)chunk_idx * CAN_RPC_RESP_CHUNK_BYTES;
    if (off + chunk_len > CAN_RPC_MAX_RESP_LEN) {
        return;
    }

    if (chunk_len > 0) {
        memcpy(&s_resp_asm.resp[off], &data[5], chunk_len);
    }
    s_resp_asm.received[chunk_idx] = 1;

    for (uint8_t i = 0; i < total; i++) {
        if (s_resp_asm.received[i] == 0) {
            return;
        }
    }

    size_t raw_len = (size_t)total * CAN_RPC_RESP_CHUNK_BYTES;
    if (raw_len > CAN_RPC_MAX_RESP_LEN) {
        raw_len = CAN_RPC_MAX_RESP_LEN;
    }
    while (raw_len > 0 && s_resp_asm.resp[raw_len - 1] == 0) {
        raw_len--;
    }
    s_resp_asm.resp[raw_len] = 0;

    const char *text = s_resp_asm.resp[0] ? s_resp_asm.resp : "(empty)";
    ESP_LOGI(TAG, "RESP from=%u seq=%u status=%u len=%u", (unsigned)src, (unsigned)seq8, (unsigned)s_resp_asm.status,
             (unsigned)strlen(text));

    // Also forward the reconstructed response to BLE (instead of raw frame spam).
    {
        char hdr[64];
        int n = snprintf(hdr, sizeof(hdr), "FROM %u (status=%u):\n", (unsigned)src, (unsigned)s_resp_asm.status);
        if (n > 0) {
            can_iface_ble_write(hdr);
        }
        can_iface_ble_write(text);
        // Ensure a trailing newline boundary.
        if (text[0] != 0) {
            size_t tl = strlen(text);
            if (tl == 0 || text[tl - 1] != '\n') {
                can_iface_ble_write("\n");
            }
        } else {
            can_iface_ble_write("\n");
        }
    }

    resp_asm_reset();
}

void can_rpc_on_frame(const twai_message_t *msg)
{
    if (msg == NULL) {
        return;
    }

    if ((msg->flags & TWAI_MSG_FLAG_EXTD) == 0) {
        return;
    }

    can_rpc_type_t type;
    uint8_t dst;
    uint8_t src;
    uint8_t seq4;
    if (!can_rpc_parse_id(msg->identifier, &type, &dst, &src, &seq4)) {
        return;
    }
    (void)seq4;

    // Only process broadcast frames or frames addressed to us.
    if (dst != CAN_RPC_BROADCAST_ID && dst != s_node_id) {
        return;
    }

    const uint8_t *d = msg->data;
    const uint8_t dlc = msg->data_length_code;

    switch (type) {
    case CAN_RPC_TYPE_PING:
        handle_ping(src, d, dlc);
        break;
    case CAN_RPC_TYPE_PONG:
        handle_pong(src, d, dlc);
        break;
    case CAN_RPC_TYPE_CMD:
        handle_cmd(dst, src, d, dlc);
        break;
    case CAN_RPC_TYPE_RESP:
        handle_resp(dst, src, d, dlc);
        break;
    default:
        break;
    }
}
