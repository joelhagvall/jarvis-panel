#pragma once
#include <algorithm>
#include <array>
#include <cstdint>
namespace panel_vinyl {
struct DamageRect {int x1,y1,x2,y2;};
inline DamageRect damage_union(DamageRect a,DamageRect b) {
  return {std::min(a.x1,b.x1),std::min(a.y1,b.y1),std::max(a.x2,b.x2),std::max(a.y2,b.y2)};
}
// Keep the opaque record a separate invalidation. An overlapping arm box would make LVGL join them and
// lose the record's opaque cover fast path, repainting the background/plinth underneath it every moving frame.
template<class Emit>void outside_record(DamageRect dirty,DamageRect record,Emit emit) {
  auto part=[&](DamageRect r){if(r.x1<=r.x2&&r.y1<=r.y2)emit(r);};
  part({dirty.x1,dirty.y1,dirty.x2,std::min(dirty.y2,record.y1-1)});
  part({dirty.x1,std::max(dirty.y1,record.y2+1),dirty.x2,dirty.y2});
  const int top=std::max(dirty.y1,record.y1),bottom=std::min(dirty.y2,record.y2);
  part({dirty.x1,top,std::min(dirty.x2,record.x1-1),bottom});
  part({std::max(dirty.x1,record.x2+1),top,dirty.x2,bottom});
}
struct ArmDamage {
  static constexpr int TILE=16,WIDTH=1024,HEIGHT=600,ROWS=(HEIGHT+TILE-1)/TILE;
  std::array<uint64_t,ROWS> rows{};
  void merge(const ArmDamage &other){for(size_t i=0;i<rows.size();++i)rows[i]|=other.rows[i];}
  void mark(const uint8_t *mask,int x,int y,int w,int h,DamageRect record) {
    for(int row=0;row<h;++row)for(int col=0;col<w;++col) {
      const int px=x+col,py=y+row;
      if(mask[row*w+col]<=3||px<0||py<0||px>=WIDTH||py>=HEIGHT||
         (px>=record.x1&&px<=record.x2&&py>=record.y1&&py<=record.y2))continue;
      rows[py/TILE]|=uint64_t(1)<<(px/TILE);
    }
  }
  template<class Emit>void emit(DamageRect record,Emit draw) {
    for(int y=0;y<ROWS;++y)while(rows[y]) {
      const int x=__builtin_ctzll(rows[y]);int end=x+1;
      while(end<64&&(rows[y]&(uint64_t(1)<<end)))++end;
      const uint64_t bits=(~uint64_t(0)<<x)&(~uint64_t(0)>>(64-end));int bottom=y+1;
      while(bottom<ROWS&&(rows[bottom]&bits)==bits)++bottom;
      for(int row=y;row<bottom;++row)rows[row]&=~bits;
      outside_record({x*TILE,y*TILE,end*TILE-1,std::min(bottom*TILE,HEIGHT)-1},record,draw);
    }
  }
  // One narrow strip is cheaper than repeatedly walking the widget tree for each sparse tile group.
  // It still cannot intersect the opaque record. Preserve sparse subtraction for unexpected geometry.
  template<class Emit>void emit_compact(DamageRect record,Emit draw) {
    ArmDamage saved=*this;DamageRect bounds{};bool any=false,right=true;
    emit(record,[&](DamageRect r){right=right&&r.x1>record.x2;bounds=any?damage_union(bounds,r):r;any=true;});
    if(!any)return;
    if(right&&int64_t(bounds.x2-bounds.x1+1)*(bounds.y2-bounds.y1+1)<=24000)draw(bounds);
    else saved.emit(record,draw);
  }
};
}
