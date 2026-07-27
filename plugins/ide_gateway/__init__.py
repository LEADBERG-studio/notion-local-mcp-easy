"""IDE Gateway plugin for notion-local-mcp-easy.

A full OpenAI-compatible API gateway that runs as an in-process MCP plugin.
The worker subprocess exposes the complete OpenAI HTTP surface (chat completions,
responses, files, images, audio, embeddings, moderations, tools) and bridges
IDE requests to the active MCP model through a file-based request queue.

The translation layer (message flattening, streaming emulation, tool bridge,
media URL extraction, local fallbacks) is ported from the standalone
hyperagent-openai-gateway project and adapted to the queue-bridge model.
"""
