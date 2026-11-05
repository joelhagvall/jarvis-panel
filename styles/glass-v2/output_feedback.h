#pragma once
#include <cstdint>
#include <string>

namespace output_feedback {
enum class Kind { NONE, CHOICE, RECONNECT };
enum class Phase { IDLE, WAITING, DONE, ERROR, TIMEOUT };
struct Operation {
  Kind kind{Kind::NONE};
  Phase phase{Phase::IDLE};
  uint32_t ticket{0}, started{0}, ended{0};
  bool accepted{false}, observed{false};
  std::string target;
  bool busy() const { return phase==Phase::WAITING; }
  bool current(uint32_t id) const { return busy()&&ticket==id; }
  bool begin(Kind which,const std::string &wanted,const std::string &actual,uint32_t now) {
    if(busy())return false;
    kind=which;target=wanted;started=now;accepted=false;
    observed=which==Kind::CHOICE && wanted==actual;
    if(++ticket==0)++ticket;
    phase=Phase::WAITING;return true;
  }
  bool complete(uint32_t now) {
    if(!busy()||!accepted||(kind==Kind::CHOICE&&!observed))return false;
    phase=Phase::DONE;ended=now;return true;
  }
  bool acknowledge(uint32_t id,uint32_t now) {
    if(!current(id))return false;
    accepted=true;return complete(now);
  }
  bool observe(const std::string &actual,uint32_t now) {
    if(!busy()||kind!=Kind::CHOICE)return false;
    observed=actual==target;return complete(now);
  }
  bool fail(uint32_t id,uint32_t now) {
    if(!current(id))return false;
    phase=Phase::ERROR;ended=now;return true;
  }
  bool tick(uint32_t now) {
    // The HA reconnect request permits 75 s; selecting a speaker permits 40 s.
    if(busy()&&uint32_t(now-started)>=(kind==Kind::RECONNECT?80000u:45000u)) {
      phase=Phase::TIMEOUT;ended=now;return true;
    }
    if(phase==Phase::DONE&&uint32_t(now-ended)>=4000) {phase=Phase::IDLE;return true;}
    return false;  // Errors remain visible until the next explicit choice/press.
  }
  std::string message() const {
    if(phase==Phase::WAITING)return kind==Kind::RECONNECT?"Kopplar om …":"Byter högtalare …";
    if(phase==Phase::DONE)return kind==Kind::RECONNECT?"Återansluten · ljudet återställs":"Vald: "+target;
    if(phase==Phase::ERROR)return kind==Kind::RECONNECT?"Kunde inte koppla om. Försök igen.":"Kunde inte byta högtalare.";
    if(phase==Phase::TIMEOUT)return "Ingen bekräftelse. Kontrollera anslutningen.";
    return "";
  }
};
inline Operation state;
}
