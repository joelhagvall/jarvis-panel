#pragma once
// The AI page after Jarvis was asked about AI usage (ai_page.py; server/agents/ai_usage.py). A request that comes
// during the voice turn waits here until the turn ends, like Spelas nu (music_jump.h).
namespace panel_ai_jump {

inline bool pending = false;

inline bool take() {
  const bool was = pending;
  pending = false;
  return was;
}

}  // namespace panel_ai_jump
