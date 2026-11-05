#pragma once
// Sparse, lossless angular atlas. Fully covered pixels stay in the static layer. Two complete atlases fit
// where two square atlases would not, so preparing the next cover never destroys the one still turning.
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <vector>
namespace panel_vinyl {
constexpr int HALF = 54;
// The orb prefetch runs at priority 1. Background cover preparation must yield to it.
struct WorkerPriority {
  int foreground{2}, current{0};
  template<class Apply> void update(bool visible,Apply apply) {
    const int wanted=visible?foreground:0;
    if(wanted!=current){current=wanted;apply(wanted);}
  }
};
// Borrow the submitted frame's idle window: leave 2.5 ms for full batches, 0.5 ms for a single late row.
// Unavailable clocks and the first atlas keep the normal bounded path.
// With a finished image already queued for the next boundary (vinyl.h's queue), a slower record image cannot cost a
// frame, so preparation may use the whole frame: a new cover took 5.4 s on the idle windows alone (2026-10-29).
inline int atlas_budget(bool foreground,bool clock,bool pending,uint32_t age_us,bool have_atlas,bool spare=false) {
  if(!foreground||!clock||!have_atlas||spare)return 4;
  if(!pending)return 0;
  return age_us<14000?4:age_us<16000?1:0;
}
inline uint32_t spread565(uint32_t p) { return (p | (p << 16)) & 0x07E0F81Fu; }
inline uint16_t pack565(uint32_t p) { p &= 0x07E0F81Fu; return uint16_t(p | (p >> 16)); }
inline uint32_t mix565(uint32_t a,uint32_t b,uint32_t w) {
  return (((a<<5)+(b-a)*w)>>5)&0x07E0F81Fu;
}
// Keep both PSRAM reads forwards; reversing in the existing internal tile avoids backwards cache misses.
inline void reverse_staged(uint16_t *pixels,size_t count,uint16_t *scratch,size_t capacity) {
  if(!scratch||capacity<2){std::reverse(pixels,pixels+count);return;}
  size_t left=0,right=count;const size_t half=capacity/2;
  while(right-left>1) {
    const size_t n=std::min(half,(right-left)/2);
    std::memcpy(scratch,pixels+left,n*2);std::memcpy(scratch+half,pixels+right-n,n*2);
    std::reverse_copy(scratch+half,scratch+half+n,pixels+left);
    std::reverse_copy(scratch,scratch+n,pixels+right-n);left+=n;right-=n;
  }
}
struct Span {int y,x,length;size_t packed;};
struct Layout {
  int size{};size_t count{};
  std::vector<Span> spans;
  void make(int n,const uint8_t *alpha) {
    size=n;count=0;spans.clear();const float r=n/2.0f-.5f;
    auto varying=[&](int x,int y){
      const int i=y*n+x;
      if(alpha)return alpha[i]<252 || alpha[n*n-1-i]<252;
      const float dx=x+.5f-n/2.0f,dy=y+.5f-n/2.0f;return dx*dx+dy*dy<=r*r;
    };
    for(int y=0;y<n;++y)for(int x=0;x<n;) {
      if(!varying(x,y)){++x;continue;}
      const int first=x;while(x<n&&varying(x,y))++x;
      spans.push_back({y,first,x-first,count});count+=x-first;
    }
  }
};
// A bounded row batch lets the same worker present the old atlas between preparation steps.
inline void prepare_rows(const uint16_t *src,int n,uint16_t *dst,const Layout &layout,int first,int end,
                         float degrees,const uint16_t *layer,const uint8_t *alpha) {
  constexpr float PI=3.14159265f;
  const int size=layout.size;const float k=float(n)/size,a=degrees*PI/180,c=std::cos(a)*k,s=std::sin(a)*k;
  const int32_t du=int32_t(c*65536),dv=int32_t(-s*65536);
  const auto begin=std::lower_bound(layout.spans.begin(),layout.spans.end(),first,[](const Span &span,int y){return span.y<y;});
  const auto finish=std::lower_bound(begin,layout.spans.end(),end,[](const Span &span,int y){return span.y<y;});
  // A whole diagonal row walks across more source cache lines than fit in the P4 cache. Reuse a small
  // source patch across this row batch before moving right. Keep the original row's fixed-point origin.
  for(int tile=0;tile<size;tile+=32)for(auto it=begin;it!=finish;++it) {
    const auto &span=*it;const int left=std::max(span.x,tile),right=std::min(span.x+span.length,tile+32);
    if(left>=right)continue;
    const float fy=span.y+.5f-size/2.0f,fx=span.x+.5f-size/2.0f;
    int32_t u=int32_t((n/2.0f+fx*c+fy*s-.5f)*65536)+du*(left-span.x),
            v=int32_t((n/2.0f-fx*s+fy*c-.5f)*65536)+dv*(left-span.x);
    for(int x=left-span.x;x<right-span.x;++x,u+=du,v+=dv) {
      int ix=u>>16,iy=v>>16;uint32_t wx=(u>>11)&31,wy=(v>>11)&31;
      if(ix<0){ix=0;wx=0;}if(iy<0){iy=0;wy=0;}
      if(ix>=n-1){ix=n-2;wx=31;}if(iy>=n-1){iy=n-2;wy=31;}
      const uint16_t *p=src+iy*n+ix;
      const uint32_t top=mix565(spread565(p[0]),spread565(p[1]),wx);
      const uint32_t bottom=mix565(spread565(p[n]),spread565(p[n+1]),wx);
      uint32_t value=mix565(top,bottom,wy);
      const int i=span.y*size+span.x+x;
      if(alpha){const uint32_t w=(uint32_t(alpha[i])+4)>>3;if(w)value=mix565(value,spread565(layer[i]),w);}
      dst[span.packed+x]=pack565(value);
    }
  }
}
// Expand a packed result inside the larger output buffer. Reverse traversal preserves unread packed data.
inline void expand_in_place(uint16_t *buffer,const Layout &layout,const uint16_t *layer) {
  size_t tail=size_t(layout.size)*layout.size;
  for(auto it=layout.spans.rbegin();it!=layout.spans.rend();++it) {
    const size_t start=it->y*layout.size+it->x,end=start+it->length;
    if(tail>end){if(layer)std::memcpy(buffer+end,layer+end,(tail-end)*2);else std::memset(buffer+end,0,(tail-end)*2);}
    std::memmove(buffer+start,buffer+it->packed,it->length*2);tail=start;
  }
  if(tail){if(layer)std::memcpy(buffer,layer,tail*2);else std::memset(buffer,0,tail*2);}
}
// A composited arm may have touched the still surround on the previous use of this output slot.
inline void restore_static(uint16_t *buffer,const Layout &layout,const uint16_t *layer) {
  size_t start=0;
  for(const auto &span:layout.spans) {
    const size_t end=span.y*layout.size+span.x;
    if(end>start){if(layer)std::memcpy(buffer+start,layer+start,(end-start)*2);else std::memset(buffer+start,0,(end-start)*2);}
    start=end+span.length;
  }
  const size_t end=size_t(layout.size)*layout.size;
  if(end>start){if(layer)std::memcpy(buffer+start,layer+start,(end-start)*2);else std::memset(buffer+start,0,(end-start)*2);}
}
// All atlas pixels are lossless RGB565. Interpolate adjacent angles only during a speed change, providing
// sub-degree movement instead of holding the same 3.33-degree image. At full speed copy a sharp atlas frame.
// Which two atlas images an angle falls between, `step` images apart (2 while only every other image of a new
// cover is built: vinyl.h turns it from half its atlas), and the 0..32 weight of the second.
struct Pick { int lo, hi; uint32_t w; };
inline Pick pick(float angle,bool interpolate,int step=1) {
  const float at=angle/(180.0f/HALF)/step;
  Pick p{};p.w=uint32_t((at-std::floor(at))*32);
  p.lo=(int(std::floor(at))*step)%(2*HALF);p.hi=(p.lo+step)%(2*HALF);
  if(!interpolate){p.lo=(int(at+.5f)*step)%(2*HALF);p.hi=p.lo;p.w=0;}
  return p;
}
// `frame(i)` gives atlas image i (0..HALF-1): one contiguous block, or separately allocated images on the panel.
template<class Frame>
inline void atlas_frame_at(Frame frame,uint16_t *dst,const Layout &layout,float angle,bool interpolate,
                           uint16_t *row_scratch=nullptr,size_t scratch_pixels=0,int step=1) {
  const auto chosen=pick(angle,interpolate,step);
  const int lo=chosen.lo,hi=chosen.hi;const uint32_t w=chosen.w;
  const uint16_t *a=frame(lo%HALF),*b=frame(hi%HALF);
  const bool flip_a=lo>=HALF,flip_b=hi>=HALF;
  for(const auto &span:layout.spans) {
    uint16_t *out=dst+span.y*layout.size+span.x;
    if(w&&row_scratch&&scratch_pixels>=size_t(span.length)*2) {
      auto *sa=row_scratch,*sb=row_scratch+span.length;
      std::memcpy(sa,a+(flip_a?layout.count-span.packed-span.length:span.packed),span.length*2);
      std::memcpy(sb,b+(flip_b?layout.count-span.packed-span.length:span.packed),span.length*2);
      for(int x=0;x<span.length;++x)out[x]=pack565(mix565(spread565(sa[flip_a?span.length-1-x:x]),
          spread565(sb[flip_b?span.length-1-x:x]),w));
      continue;
    }
    if(!w&&!flip_a){std::memcpy(out,a+span.packed,span.length*2);continue;}
    if(!w&&flip_a&&row_scratch&&scratch_pixels>=size_t(span.length)) {
      // PSRAM prefetch works forwards. Fetch one row forwards into the existing internal tile, reverse there,
      // and write forwards, instead of making every source-cache miss walk backwards through PSRAM.
      std::memcpy(row_scratch,a+layout.count-span.packed-span.length,span.length*2);
      std::reverse_copy(row_scratch,row_scratch+span.length,out);continue;
    }
    for(int x=0;x<span.length;++x) {
      const size_t i=span.packed+x;
      const uint16_t pa=a[flip_a?layout.count-1-i:i];
      if(!w){out[x]=pa;continue;}
      const uint16_t pb=b[flip_b?layout.count-1-i:i];
      out[x]=pack565(mix565(spread565(pa),spread565(pb),w));
    }
  }
}
inline void atlas_frame(const uint16_t *atlas,uint16_t *dst,const Layout &layout,float angle,bool interpolate,
                        uint16_t *row_scratch=nullptr,size_t scratch_pixels=0) {
  atlas_frame_at([&](int i){return atlas+size_t(i)*layout.count;},dst,layout,angle,interpolate,row_scratch,scratch_pixels);
}
}  // namespace panel_vinyl
