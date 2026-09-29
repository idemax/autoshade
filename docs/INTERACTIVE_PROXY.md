# Interactive AI proxy

`tools/autoshade-claude-proxy.py` is a file-backed replacement for the
Claude executable used by AutoShade's analysis verifier. AutoShade still sends
the verifier prompt through standard input and still receives the Claude JSON
envelope on standard output. The proxy only changes the middle step: it saves
the prompt, waits for a response file, validates the verdict, and returns it
to AutoShade.

## Configure

```sh
export AUTOSHADE_CLAUDE_BIN="/absolute/path/to/autoshade/tools/autoshade-claude-proxy.py"
export AUTOSHADE_PROXY_DIR="/absolute/path/to/autoshade-proxy"
export AUTOSHADE_PROXY_TIMEOUT_SECS=3600
```

The normal AutoShade command can then be used unchanged. Each request creates
two files:

```text
autoshade-proxy/requests/<id>.txt   prompt to inspect
autoshade-proxy/requests/<id>.json  request metadata
autoshade-proxy/responses/<id>.json response supplied by the operator
```

The response JSON is:

```json
{
  "decision": "accept",
  "reasons": [],
  "revised_hint": null
}
```

`decision` must be `accept`, `revise`, or `reject`. For `revise` and
`reject`, `revised_hint` can contain the instruction for the next proposal
round. The proxy never writes a recipe or pixels; AutoShade remains responsible
for validating the verdict, saving the recipe, and rendering the RAW.

This bridge is intentionally interactive. It does not claim that the current
ChatGPT conversation is an API endpoint and it does not copy credentials into
request files.
