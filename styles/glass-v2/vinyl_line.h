#pragma once
// Cache a rounded line's A8 coverage instead of regenerating LVGL's complex line masks at every disc redraw.
#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include "vinyl_damage.h"
#include "vinyl_sampler.h"

namespace panel_vinyl {
constexpr std::array<size_t,6> ARM_MASK_CAPACITY{2048,0,16384,2048,2048,1024};
struct LineMask {
  uint8_t *pixels{};size_t capacity{};
  int x{},y{},w{},h{};
  ArmDamage coverage;
  float last_x0{-10000},last_y0{},last_x1{},last_y1{},last_width{};
  bool paint(float x0,float y0,float x1,float y1,float width) {
    if(!pixels)return false;
    if(x0==last_x0&&y0==last_y0&&x1==last_x1&&y1==last_y1&&width==last_width)return true;
    const float radius=width*.5f;
    const int left=int(std::floor(std::min(x0,x1)-radius-1)),top=int(std::floor(std::min(y0,y1)-radius-1));
    const int right=int(std::ceil(std::max(x0,x1)+radius+1)),bottom=int(std::ceil(std::max(y0,y1)+radius+1));
    const int wide=right-left+1,high=bottom-top+1;
    if(wide<=0||high<=0||size_t(wide)*high>capacity)return false;
    coverage.rows.fill(0);
    std::memset(pixels,0,size_t(wide)*high);
    const float dx=x1-x0,dy=y1-y0,den=dx*dx+dy*dy,inv=den>0?1/den:0;
    const float outer=radius+.5f,inner=radius-.5f,inv_dy=dy!=0?1/dy:0;
    for(int row=0;row<high;++row) {
      int first=0,end=wide;
      if(dy!=0) {
        const float a=(top+row-y0-outer)*inv_dy,b=(top+row-y0+outer)*inv_dy;
        const float lo=std::max(0.0f,std::min(a,b)),hi=std::min(1.0f,std::max(a,b));
        if(lo>hi)continue;
        const float xa=x0+dx*lo,xb=x0+dx*hi;
        first=std::max(0,int(std::floor(std::min(xa,xb)-outer-1))-left);
        end=std::min(wide,int(std::ceil(std::max(xa,xb)+outer+1))-left+1);
      }
      for(int col=first;col<end;++col) {
      const float px=left+col-x0,py=top+row-y0;
      const float t=std::clamp((px*dx+py*dy)*inv,0.0f,1.0f);
      const float a=px-t*dx,b=py-t*dy;
      const float distance2=a*a+b*b;
      // Only the one-pixel antialiased fringe needs a square root. Interior/transparent pixels are exact.
      const uint8_t alpha=distance2>=outer*outer?0:inner>=0&&distance2<=inner*inner?255:
        uint8_t(std::clamp(outer-std::sqrt(distance2),0.0f,1.0f)*255+.5f);
      pixels[row*wide+col]=alpha;
      const int screen_x=left+col,screen_y=top+row;
      if(alpha>3&&screen_x>=0&&screen_x<ArmDamage::WIDTH&&screen_y>=0&&screen_y<ArmDamage::HEIGHT)
        this->coverage.rows[screen_y/ArmDamage::TILE]|=uint64_t(1)<<(screen_x/ArmDamage::TILE);
      }
    }
    x=left;y=top;w=wide;h=high;
    last_x0=x0;last_y0=y0;last_x1=x1;last_y1=y1;last_width=width;
    return true;
  }
};
constexpr std::array<uint16_t,6> ARM_COLORS{0x18C3,0,0x2966,0x4A6B,0x18C3,0xE75D};
// Same five-bit RGB565 blend as LVGL. Draw only the square's intersection, in widget stacking order.
inline void composite_arm_rect(uint16_t *record,int width,int height,int x,int y,const std::array<LineMask,6> &masks) {
  for(int i:{0,2,3,4,5}) {
    const auto &m=masks[i];
    const int left=std::max(x,m.x),right=std::min(x+width,m.x+m.w);
    const int top=std::max(y,m.y),bottom=std::min(y+height,m.y+m.h);
    for(int py=top;py<bottom;++py)for(int px=left;px<right;++px) {
      const uint8_t alpha=m.pixels[(py-m.y)*m.w+px-m.x];if(!alpha)continue;
      auto &out=record[(py-y)*width+px-x];const auto color=ARM_COLORS[i];
      if(alpha==255)out=color;
      else {const uint32_t bg=spread565(out),fg=spread565(color),w=(uint32_t(alpha)+4)>>3;
        out=pack565(((((fg-bg)*w)>>5)+bg)&0x07E0F81Fu);}
    }
  }
}
inline void composite_arm(uint16_t *record,int size,int x,int y,const std::array<LineMask,6> &masks) {
  composite_arm_rect(record,size,size,x,y,masks);
}
}
