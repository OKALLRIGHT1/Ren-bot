# Search Acknowledgement and Deterministic Execution

## Problem

An explicit web-search request can stop after the assistant says that it will
search. The search plugin is configured as `react` while its implementation
requires delegate mode. If tool reasoning falls back to a model that returns a
plain acknowledgement instead of a command, no search runs and that
acknowledgement becomes the final reply.

## Required Behavior

- Explicit web-search requests send one short acknowledgement immediately.
- The search runs through the delegate path without depending on a chat model
  to emit a `[CMD: ...]` command.
- The completed search result is sent as a second reply.
- Search failures also produce a second, explicit failure reply.
- Existing follow-up topic resolution remains unchanged.
- Non-search tools and normal chat output remain unchanged.

## Design

Restore the search plugin's configured type to `delegate`, matching the plugin
class and its `delegate_mode` runtime guard. Once routing identifies a search
delegate, `ChatService` emits a fixed, short acknowledgement through the
existing assistant output path before executing the delegate flow. The normal
tool finalization path then sends the result as a separate reply and retains
the existing fallback that exposes search output when final response generation
fails.

The acknowledgement is emitted only for a routed search delegate and only once
per incoming request. It is not merged into the final answer. This keeps the
two messages semantically distinct and prevents a model-generated promise from
being mistaken for task completion.

## Error Handling

The acknowledgement confirms that execution started, not that it succeeded.
If the search provider or model fails, the delegate result is finalized into a
clear second reply. No request should finish with only the acknowledgement.

## Verification

- Add a regression test showing that `search_web` is loaded as a delegate.
- Add a chat-flow test asserting an acknowledgement is emitted before search
  execution and a final result is emitted afterward.
- Add or retain coverage ensuring a failed final model response falls back to
  the available search result.
- Run the focused search, tool-flow, and chat-service tests.
