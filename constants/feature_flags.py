# constants/feature_flags.py
PERSISTENT_AGENTS = "persistent_agents"
ORGANIZATIONS = "organizations"
MULTIPLAYER_AGENTS = "multiplayer_agents"

# Soft-expiration for free-plan agents that go inactive
AGENT_SOFT_EXPIRATION = "agent_soft_expiration"



# Are we allow to send to multiple comm points at once - NOTE THIS IS NOT THE SAME AS MULTIPLAYER_AGENTS
# This is a switch to send to multiple comms points at once, such as email and sms, or multiple emails. has to be a
# switch not flag
MULTISEND_ENABLED = "multisend_enabled"