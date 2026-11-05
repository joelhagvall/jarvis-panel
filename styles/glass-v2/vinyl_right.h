#pragma once
#include <array>
#include <cstring>
#include "vinyl_line.h"
namespace panel_vinyl {
// ESPHome RGB565A8 descriptors declare only the RGB plane's size; the format guarantees a following A8
// plane. These are compiled static plinth assets, not untrusted runtime images.
inline bool solid_top_region(const uint8_t *data,size_t rgb_bytes,int stride) {
  const size_t rgb=size_t(stride)*43*2;if(!data||stride<407||rgb_bytes<rgb)return false;
  const auto *alpha=data+rgb;
  for(int y=33;y<43;++y)for(int x=331;x<395;++x)if(alpha[y*stride+x]!=255)return false;
  return true;
}
inline void alpha_from_argb(const uint8_t *bgra,uint8_t *alpha,size_t pixels) {
  for(size_t i=0;i<pixels;++i)alpha[i]=bgra[i*4+3];
}
inline bool valid_pivot(const uint8_t *alpha,int w,int h) {
  return alpha&&w>0&&h>0&&alpha[(h/2)*w+w/2]==255;
}
struct RightArm {
  static constexpr int X=360,Y=100,W=64,H=236;
  static constexpr size_t BYTES=W*H*2;
  // The record's three queued images (shown, ready, in work) may each name their own strip; one more is always
  // free to paint, so the worker never writes a strip LVGL or a queued image still uses.
  static constexpr int POOL=4;
  using Pose=std::array<int,14>;
  uint16_t *pixels[POOL]{},*base{},*rest{};
  uint8_t *pivot{};
  const uint16_t *top_rgb{},*right_rgb{};
  int top_stride{},right_stride{};
  DamageRect base_rect{},rest_rect{},pivot_rect{};
  uint16_t pivot_color{};
  Pose shown_pose{},asked_pose{};
  int displayed{-1},target{},ready{-1};bool enabled{},have_pose{},wanted{};
  int shown{-1};  // the pool strip LVGL shows (Disc's queue)
  void request(Pose pose) {
    asked_pose=pose;wanted=!have_pose||pose!=shown_pose;target=displayed==0?1:0;
  }
  void background(uint16_t *out)const {
    for(int y=0;y<H;++y) {
      const auto *src=y+Y<110 ? top_rgb+(y+Y-67)*top_stride+(X-29) : right_rgb+(y+Y-110)*right_stride;
      std::memcpy(out+y*W,src,W*2);
    }
  }
  void paste(uint16_t *out,const uint16_t *sprite,DamageRect r)const {
    const int w=r.x2-r.x1+1,h=r.y2-r.y1+1;
    for(int y=0;y<h;++y)std::memcpy(out+(r.y1-Y+y)*W+r.x1-X,sprite+y*w,w*2);
  }
  void render(const std::array<LineMask,6>& masks) {
    ready=-1;if(!wanted)return;
    paint(pixels[target],masks);ready=target;
  }
  void paint(uint16_t *out,const std::array<LineMask,6>& masks)const {
    background(out);paste(out,rest,rest_rect);paste(out,base,base_rect);
    composite_arm_rect(out,W,H,X,Y,masks);
    const int w=pivot_rect.x2-pivot_rect.x1+1,h=pivot_rect.y2-pivot_rect.y1+1;
    for(int y=0;y<h;++y)for(int x=0;x<w;++x){
      const uint32_t alpha=pivot[y*w+x];if(!alpha)continue;
      auto &p=out[(pivot_rect.y1-Y+y)*W+pivot_rect.x1-X+x];
      if(alpha==255)p=pivot_color;
      else {const uint32_t bg=spread565(p),fg=spread565(pivot_color),weight=(alpha+4)>>3;
        p=pack565(((((fg-bg)*weight)>>5)+bg)&0x07E0F81Fu);}
    }
  }
  void consume(bool visible) {
    if(visible&&ready>=0){displayed=ready;shown_pose=asked_pose;have_pose=true;}
    ready=-1;
  }
};
}
