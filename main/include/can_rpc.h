#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "driver/twai.h"

#ifdef __cplusplus
extern "C" {
#endif

// Broadcast node ID used by this simple CAN RPC protocol.
#define CAN_RPC_BROADCAST_ID 0xFF

// Initialize CAN RPC state (auto-selects a default node ID derived from chip-unique eFuse identity).
void can_rpc_init(void);

// Current node ID (1-254). 0xFF reserved for broadcast.
uint8_t can_rpc_get_node_id(void);

// Set node ID at runtime (not persisted yet). Returns false if invalid.
bool can_rpc_set_node_id(uint8_t node_id);

typedef struct {
	uint8_t node_id;
	uint8_t mac[6];
	// Age since last PONG from this node.
	uint32_t age_ms;
} can_rpc_neighbor_info_t;

// Copy up to max_out neighbors into out (may be NULL to query count).
// Returns number of neighbors copied (or total neighbors if out is NULL).
size_t can_rpc_get_neighbors(can_rpc_neighbor_info_t *out, size_t max_out);

// Broadcast a discovery ping; all nodes respond with their info.
void can_rpc_ping_all(void);

// Send an addressed command to another node.
// The remote node will execute it as if typed into its console, and will respond.
bool can_rpc_send_cmd(uint8_t dst_node_id, const char *cmd);

// Broadcast a command to all nodes. Nodes will execute it but will not send responses.
bool can_rpc_send_cmd_broadcast(const char *cmd);

// Frame hook called by can_iface for each received CAN frame.
void can_rpc_on_frame(const twai_message_t *msg);

// Returns true if the CAN identifier is part of the CAN RPC protocol (extended ID with matching proto field).
bool can_rpc_is_id(uint32_t id);

#ifdef __cplusplus
}
#endif
