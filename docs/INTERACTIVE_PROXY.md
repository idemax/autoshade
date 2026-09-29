# Interactive AI proxy

This folder contains two file-backed adapters for a fully local interactive
AutoShade run:

* `tools/autoshade-openai-proxy.py` replaces the Responses API used by the
  visual proposer.
* `tools/autoshade-claude-proxy.py` replaces the Claude executable used by the
  data-only verifier.

AutoShade still uses its normal provider contracts. The adapters only change
the middle step: they save prompts and preview images, wait for response files,
validate the response shape, and return it to AutoShade.

## Configure

```sh
export AUTOSHADE_CLAUDE_BIN="/absolute/path/to/autoshade/tools/autoshade-claude-proxy.py"
export AUTOSHADE_PROXY_DIR="/absolute/path/to/autoshade-proxy"
export AUTOSHADE_PROXY_TIMEOUT_SECS=3600
export OPENAI_API_KEY="local-proxy-placeholder"
export AUTOSHADE_OPENAI_BASE_URL="http://127.0.0.1:8317/v1"
```

Start the proposer adapter in one terminal before running AutoShade:

```sh
python3 /absolute/path/to/autoshade/tools/autoshade-openai-proxy.py
```

The normal AutoShade command can then be used unchanged. Each request creates
two files:

```text
autoshade-proxy/requests/<id>.txt   prompt to inspect
autoshade-proxy/requests/<id>.json  request metadata
autoshade-proxy/requests/<id>.prompt.txt  proposer prompt to inspect
autoshade-proxy/requests/<id>.image-*.jpg  proposer preview images
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

For a proposer request, the same response path must contain the recipe JSON
itself. The request metadata includes the exact schema under `schema`, so the
operator can return only the fields needed for the edit and let AutoShade apply
its normal defaults and limits.

This bridge is intentionally interactive. It does not claim that the current
ChatGPT conversation is an API endpoint, binds the HTTP adapter to loopback by
default, and does not copy credentials into request files. `OPENAI_API_KEY` is
only a non-secret placeholder needed to activate AutoShade's existing proposer
branch; the local adapter never sends it anywhere.
