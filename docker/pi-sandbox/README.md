# Isolated native Pi generation

Build from a local Linux Node >=22.19 binary, this Dockerfile and the pinned
Python requirements. Keep the binary outside Git, for example in a temporary
build context under the experiment directory. The image does not contain model
weights or a host user profile.

`run_inference(..., sandbox_image=...)` mounts one condition's workspace as
`/workspace` plus read-only runner/dependency files. Other cases, host HOME and
the Docker socket are not mounted. Each container has its own `/tmp`, process
namespace, read-only root and resource limits. An optional `sandbox_fonts`
directory is mounted read-only into the font directory for both conditions.
This is an execution boundary for the study, not a claim of a complete defense
against an adversarial host or unrestricted network services.

The bridge-network container needs a reachable native Messages endpoint.
`tools/pi_recording_proxy.py` can bind per-pair endpoints on the bridge gateway
and forward unchanged bodies to a localhost backend. Both conditions of a pair
use the same endpoint; active logs rotate with the paired controller. Unlike the
regular onPayload hook alone, this transport recorder also captures requests
made through native compaction's separate provider path. It records request
bodies and response status/timing; it does not claim a raw response-token wire
capture.

Set `tool_policy.bash_timeout_seconds` to enforce a maximum on every Bash call,
even when the model omits or exceeds the timeout. Timeouts return real native
tool errors and are retained. `finish_after_structural_export` adds a common
instruction to end after a successful export/open check. Apply the same policy
to both conditions and freeze it before the first model request. Existing host
mode and omitted-policy behavior remain available for historical reproduction.
