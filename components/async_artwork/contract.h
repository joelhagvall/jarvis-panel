#pragma once
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <string>
namespace artwork_contract {
constexpr int SIDE=288;
constexpr size_t PIXELS=SIDE*SIDE, MAX_BYTES=PIXELS*5+22;
inline uint64_t picture_identity(const uint8_t *data,size_t bytes) {
  uint64_t value=1469598103934665603ull;
  for(size_t i=0;i<bytes;i+=7)value=(value^data[i])*1099511628211ull;
  return (value^bytes)*1099511628211ull;
}
inline bool header(const uint8_t *p,size_t n) {
  return n>=14 && std::memcmp(p,"qoif",4)==0 &&
      p[4]==0 && p[5]==0 && p[6]==1 && p[7]==32 &&
      p[8]==0 && p[9]==0 && p[10]==1 && p[11]==32 &&
      (p[12]==3 || p[12]==4) && p[13]<=1;
}
inline bool key_url(const std::string &url,const std::string &prefix) {
  if(url.size()!=prefix.size()+36 || url.compare(0,prefix.size(),prefix)!=0 ||
     url.compare(url.size()-4,4,".qoi")!=0)return false;
  for(size_t i=prefix.size();i<url.size()-4;++i)
    if(!((url[i]>='0'&&url[i]<='9')||(url[i]>='a'&&url[i]<='f')))return false;
  return true;
}
// Keep the eight-byte QOI terminator outside the upstream decoder, which reports an entire final chunk
// consumed even when its last pixel precedes the terminator.
template<class Decoder>bool feed(Decoder &decoder,uint8_t *buffer,size_t &used) {
  if(!decoder.is_decode_finished()&&used>8) {
    const size_t available=used-8;const int consumed=decoder.feed_data(buffer,available);
    if(consumed<0||size_t(consumed)>available)return false;
    used-=consumed;std::memmove(buffer,buffer+consumed,used);
  }
  return decoder.is_decode_finished()?used==8:used<=32;
}
// The published slot cannot become a decoder target until the main thread publishes its replacement.
struct Handoff {
  int shown{-1};
  int target() const {return shown==0?1:0;}
  void publish(int slot) {shown=slot;}
};
// The next cover downloaded ahead into the free slot (prefetch), main thread only. A later update for the same
// URL publishes that slot at once instead of downloading; any other download or a publish of that slot ends it.
struct Ahead {
  std::string url;
  int slot{-1};
  bool hit(const std::string &wanted,int target) const {return slot>=0&&slot==target&&!wanted.empty()&&url==wanted;}
  void keep(const std::string &done,int done_slot) {url=done;slot=done_slot;}
  void forget() {url.clear();slot=-1;}
};
}  // namespace artwork_contract
