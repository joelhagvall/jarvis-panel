"""Real LVGL pixels and invalidation for static-page updates, without panel access."""
from pathlib import Path
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class StaticUpdateTest(unittest.TestCase):
    def test_local_curve_pixels_and_unchanged_updates(self):
        build = ROOT/'styles/glass-v2/.esphome/build/style-glass-v2'
        libraries = list(build.glob('.pioenvs/*/lib*/liblvgl.a'))
        headers = list(build.glob('.piolibdeps/*/lvgl/lvgl.h'))
        self.assertEqual((len(libraries), len(headers)), (1, 1), 'Native LVGL dependency required')
        source = r'''
#include "lvgl.h"
#include "src/display/lv_display_private.h"
#include "styles/glass-v2/ui_update.h"
#include "styles/glass-v2/ui_priority_lvgl.h"
#include <cassert>
#include <cstdlib>
#include <cstring>
#include <map>
#include <cstdio>
#include "styles/glass-v2/weather_view.h"
#include "styles/glass-v2/calendar_color.h"
static std::map<std::string,lv_obj_t *> widgets;
#define id(name) widgets.at(#name)
static void calendar(const std::string &x){ CALENDAR_FUNCTION }
static void forecast_week(const std::string &x){ WEATHER_FUNCTION }
static void forecast_hours(const std::string &x){ WEATHER_HOURS_FUNCTION }
extern "C" {
void lv_mem_init() {} void lv_mem_deinit() {}
void *lv_malloc_core(size_t n){return std::malloc(n);}
void lv_free_core(void *p){std::free(p);}
void *lv_realloc_core(void *p,size_t n){return std::realloc(p,n);}
lv_result_t lv_mem_test_core(){return LV_RESULT_OK;}
void lv_mem_monitor_core(lv_mem_monitor_t *p){std::memset(p,0,sizeof(*p));}
}
static unsigned flushed=0;
namespace esphome {uint32_t fake_ms=1000;uint32_t millis(){return fake_ms;}}
static bool pointer_down=false,background_applied=false;
int main(int argc,char **){
 lv_init();auto *d=lv_display_create(1024,600);
 alignas(64) static unsigned char pixels[1024*600*4],reference[1024*600*2];
 lv_display_set_color_format(d,LV_COLOR_FORMAT_RGB565);
 lv_display_set_buffers(d,pixels,nullptr,sizeof(pixels),LV_DISPLAY_RENDER_MODE_FULL);
 lv_display_set_flush_cb(d,[](lv_display_t *d,const lv_area_t *,uint8_t *){++flushed;lv_display_flush_ready(d);});
 auto *screen=lv_screen_active();lv_obj_remove_style_all(screen);
 lv_obj_set_style_bg_color(screen,lv_color_hex(0x123456),0);lv_obj_set_style_bg_opa(screen,255,0);
 auto *line=lv_line_create(screen);lv_obj_set_style_line_width(line,3,0);
 lv_obj_set_style_line_rounded(line,true,0);lv_obj_set_style_line_color(line,lv_color_hex(0xffffff),0);
 lv_point_precise_t world[24],local[24];
 for(int test=0;test<24;++test){
   for(int i=0;i<24;++i){world[i]={(lv_value_precise_t)(48+i*928/23),(lv_value_precise_t)(196+(i*31+test*19)%105)};
     local[i]={world[i].x-48,world[i].y-196};}
   lv_obj_set_pos(line,0,0);lv_obj_set_size(line,LV_SIZE_CONTENT,LV_SIZE_CONTENT);lv_line_set_points(line,world,24);
   lv_obj_invalidate(screen);lv_refr_now(d);std::memcpy(reference,pixels,sizeof(reference));
   lv_obj_set_pos(line,48,196);lv_obj_set_size(line,929,105);lv_line_set_points(line,local,24);
   lv_obj_invalidate(screen);lv_refr_now(d);assert(!std::memcmp(reference,pixels,sizeof(reference)));
 }
 auto *label=lv_label_create(screen);ui_update::text(label,"current");ui_update::x(label,40);
 ui_update::y(label,50);ui_update::width(label,120);ui_update::height(label,30);
 ui_update::text_opa(label,153,0);ui_update::text_color(label,lv_color_hex(0xabcdef),0);
 ui_update::bg_color(label,lv_color_hex(0x102030),0);ui_update::opacity(label,200);
 lv_refr_now(d);unsigned before=flushed;
 for(int i=0;i<100;++i){ui_update::text(label,"current");ui_update::visible(label,true);
   ui_update::x(label,40);ui_update::y(label,50);ui_update::width(label,120);ui_update::height(label,30);
   ui_update::text_opa(label,153,0);ui_update::text_color(label,lv_color_hex(0xabcdef),0);
   ui_update::bg_color(label,lv_color_hex(0x102030),0);ui_update::opacity(label,200);}
 lv_refr_now(d);assert(flushed==before);
 // Two pending geometry changes must compare the style, not the previous layout position.
 ui_update::x(label,99);ui_update::x(label,40);ui_update::visible(label,false);ui_update::visible(label,true);
 ui_update::text(label,"new");lv_refr_now(d);
 assert(lv_obj_get_x(label)==40&&!lv_obj_has_flag(label,LV_OBJ_FLAG_HIDDEN));
 assert(!std::strcmp(lv_label_get_text(label),"new")&&flushed>before);
 CALENDAR_ASSERTS
 WEATHER_ASSERTS
 // Real LVGL pointer events and a click-triggered page switch must be presented before queued data.
 auto *next=lv_obj_create(nullptr);auto *button=lv_button_create(forecast);lv_obj_set_pos(button,10,10);lv_obj_set_size(button,40,40);
 lv_obj_add_event_cb(button,[](lv_event_t *e){lv_screen_load((lv_obj_t*)lv_event_get_user_data(e));},LV_EVENT_CLICKED,next);
 lv_obj_update_layout(forecast);lv_refr_now(d);
 auto *pointer=lv_indev_create();lv_indev_set_type(pointer,LV_INDEV_TYPE_POINTER);lv_indev_set_mode(pointer,LV_INDEV_MODE_EVENT);
 lv_indev_set_read_cb(pointer,[](lv_indev_t*,lv_indev_data_t *data){data->point={20,20};data->state=pointer_down?LV_INDEV_STATE_PRESSED:LV_INDEV_STATE_RELEASED;});
 ui_priority::install();ui_priority::queue.slots[0].render=[](const std::string &){background_applied=true;};
 pointer_down=true;lv_indev_read(pointer);assert(ui_priority::queue.input.seen);
 ui_priority::queue.submit(0,"pending weather",esphome::fake_ms);
 esphome::fake_ms+=20;assert(!ui_priority::queue.tick(esphome::fake_ms,ui_priority::pointer_pressed()));
 pointer_down=false;lv_indev_read(pointer);assert(lv_screen_active()==next);
 esphome::fake_ms+=20;assert(!ui_priority::queue.tick(esphome::fake_ms,false));
 lv_refr_now(d);assert(ui_priority::queue.tick(esphome::fake_ms,false)&&background_applied);
 lv_obj_t *many[129];for(auto &o:many){o=lv_label_create(next);lv_label_set_text(o,"old");}
 lv_refr_now(d);
 {ui_update::Batch batch(next,{{0,0,1023,599}});for(auto *o:many)ui_update::text(o,"new");}
 assert(lv_display_is_invalidation_enabled(d)&&!ui_update::current_batch);
 lv_refr_now(d);std::memcpy(reference,pixels,sizeof(reference));lv_obj_invalidate(next);lv_refr_now(d);
 assert(!std::memcmp(reference,pixels,sizeof(reference))); // Capacity fallback must retain every changed area.
 PRESS_HINT_ASSERTS
}
'''
        spec=importlib.util.spec_from_file_location('calendar_page',ROOT/'styles/glass-v2/calendar_page.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        setup=[]
        for name in sorted(set(re.findall(r'id\((cw\w+)\)',module.WEEK))):
            label=any(name.endswith(s) for s in ('_title','_time','_date','_day'))
            setup.append(f'widgets["{name}"]={"lv_label_create" if label else "lv_obj_create"}(screen);')
        checks=r'''
 calendar("h|Mon|2;e|10:00|First|FF0000|0;e|11:00|Second|00FF00|1;h|Tue|3;e|12:00|Third|0000FF|0");
 assert(!lv_obj_has_flag(id(cw0_1),LV_OBJ_FLAG_HIDDEN));
 assert(!lv_obj_has_flag(id(cw1),LV_OBJ_FLAG_HIDDEN));
 calendar("h|Mon|2;e|10:00|Changed|FF0000|0");
 assert(lv_obj_has_flag(id(cw0_1),LV_OBJ_FLAG_HIDDEN));assert(lv_obj_has_flag(id(cw1),LV_OBJ_FLAG_HIDDEN));
 assert(!std::strcmp(lv_label_get_text(id(cw0_0_title)),"Changed"));
 lv_refr_now(d);before=flushed;calendar("h|Mon|2;e|10:00|Changed|FF0000|0");lv_refr_now(d);
 assert(flushed==before);
 calendar("h|Mon|2;e|10:00|First|FF0000|0;e|11:00|Second|00FF00|1;h|Tue|3;e|12:00|Third|0000FF|0");
 assert(!lv_obj_has_flag(id(cw0_1),LV_OBJ_FLAG_HIDDEN));assert(!lv_obj_has_flag(id(cw1),LV_OBJ_FLAG_HIDDEN));
 calendar("");assert(lv_obj_has_flag(id(cw0),LV_OBJ_FLAG_HIDDEN));
'''
        source=source.replace('CALENDAR_FUNCTION',module.WEEK).replace('CALENDAR_ASSERTS','\n'.join(setup)+checks)
        spec=importlib.util.spec_from_file_location('weather_page',ROOT/'styles/glass-v2/weather_page.py')
        weather=importlib.util.module_from_spec(spec);spec.loader.exec_module(weather)
        weather_checks=r'''
 auto *forecast=lv_obj_create(nullptr);lv_obj_remove_style_all(forecast);widgets["page_vader"]=forecast;
 lv_obj_set_style_bg_opa(forecast,255,0);lv_obj_set_style_bg_color(forecast,lv_color_hex(0x141826),0);
 auto plain=[](lv_obj_t *p){auto *o=lv_obj_create(p);lv_obj_remove_style_all(o);return o;};
 auto *curve=lv_line_create(forecast);widgets["wx_curve"]=curve;
 lv_obj_set_pos(curve,48,196);lv_obj_set_size(curve,929,105);lv_obj_set_style_line_width(curve,3,0);
 auto *icon=lv_label_create(forecast);widgets["wx_icon"]=icon;lv_obj_set_pos(icon,144,64);
 auto *rain=lv_label_create(forecast);widgets["wx_rain_lbl"]=rain;lv_label_set_text(rain,"rain mm/h");lv_obj_set_pos(rain,915,152);
 for(int i=0;i<8;++i){
   int x=weather_view::x_at(i*3);auto k=std::to_string(i);auto *dot=plain(forecast);
   lv_obj_set_pos(dot,x-4,0);lv_obj_set_size(dot,8,8);lv_obj_set_style_bg_opa(dot,255,0);widgets["wx_dot"+k]=dot;
   for(const char *prefix:{"wx_t","wx_i","wx_h"}){auto *l=lv_label_create(forecast);widgets[prefix+k]=l;
     lv_obj_set_width(l,60);lv_obj_set_pos(l,x-30,!std::strcmp(prefix,"wx_i")?318:!std::strcmp(prefix,"wx_h")?386:0);}
 }
 for(int i=0;i<24;++i){auto *b=plain(forecast);widgets["wx_bar"+std::to_string(i)]=b;
   lv_obj_set_pos(b,weather_view::x_at(i)-9,320);lv_obj_set_size(b,18,60);
   lv_obj_set_style_bg_color(b,lv_color_hex(0x5aaaff),0);lv_obj_set_style_bg_opa(b,153,0);lv_obj_set_style_radius(b,3,0);}
 for(int i=0;i<7;++i){
   auto key="wk"+std::to_string(i);auto *column=lv_obj_create(forecast);lv_obj_remove_style_all(column);
   lv_obj_set_pos(column,40+134*i,428);lv_obj_set_size(column,134,140);
   auto *row=lv_obj_create(column);lv_obj_remove_style_all(row);lv_obj_set_size(row,LV_SIZE_CONTENT,LV_SIZE_CONTENT);
   lv_obj_set_layout(row,LV_LAYOUT_FLEX);lv_obj_set_flex_flow(row,LV_FLEX_FLOW_ROW);
   lv_obj_set_style_pad_column(row,5,0);lv_obj_align(row,LV_ALIGN_TOP_MID,0,72);
   for(const char *suffix:{"name","icon","t_hi","t_lo","rain","gust"}){
     auto *l=lv_label_create(!std::strncmp(suffix,"t_",2)?row:column);widgets[key+"_"+suffix]=l;
     if(!std::strncmp(suffix,"t_",2))continue;
     lv_obj_align(l,LV_ALIGN_TOP_MID,0,!std::strcmp(suffix,"icon")?28:!std::strcmp(suffix,"rain")?98:!std::strcmp(suffix,"gust")?118:4);
   }
 }
 auto *outside=lv_label_create(forecast);lv_obj_set_pos(outside,10,10);
 lv_display_set_render_mode(d,LV_DISPLAY_RENDER_MODE_DIRECT);lv_screen_load(forecast);
 for(int step=0;step<20;++step){
   if(step) {
     const lv_area_t graph={16,170,991,380};lv_obj_invalidate_area(forecast,&graph);
     // The simultaneous graph changes must remain inside their predeclared region.
     for(int n=0;n<40;++n){lv_area_t dot={20+n*20,180,28+n*20,188};lv_obj_invalidate_area(forecast,&dot);}
   }
   std::string data;for(int i=0;i<7;++i){if(i)data+=';';data+="day|"+std::string(step%2?"rain":"cloud")+"|"+(step%2?"10°":"9°")+"|3°|2 mm|byar 8";}
   ui_update::text(outside,std::string(step+1,'X').c_str()); // Existing unrelated dirty layout must survive.
   std::string hours;int count=step%5==0?0:step%5==1?8:24;
   for(int i=0;i<count;++i){if(i)hours+=';';hours+=std::to_string(i)+"|rain|"+std::to_string((i*7+step)%19-4)+"|"+(step%4==0?"0":step%4==1?"2":"0.5");}
   forecast_hours(hours);
   forecast_week(data);lv_obj_update_layout(forecast);assert(lv_display_is_invalidation_enabled(d));
   if(step&&argc==1){assert(d->inv_p>0);for(unsigned n=0;n<d->inv_p;++n)
     assert(lv_area_get_width(&d->inv_areas[n])*lv_area_get_height(&d->inv_areas[n])<1024*600);}
   lv_refr_now(d);unsigned hash=2166136261u;
   for(unsigned n=0;n<sizeof(reference);++n)hash=(hash^pixels[n])*16777619u;
   std::printf("frame %d %u\n",step,hash);
   std::memcpy(reference,pixels,sizeof(reference));lv_obj_invalidate(forecast);lv_refr_now(d);
   if(std::memcmp(reference,pixels,sizeof(reference))){
     int x1=1024,y1=600,x2=0,y2=0;auto *a=(uint16_t*)reference,*b=(uint16_t*)pixels;
     for(int y=0;y<600;++y)for(int x=0;x<1024;++x)if(a[y*1024+x]!=b[y*1024+x]){
       x1=std::min(x1,x);x2=std::max(x2,x);y1=std::min(y1,y);y2=std::max(y2,y);}
     std::fprintf(stderr,"partial mismatch step %d box %d,%d,%d,%d\n",step,x1,y1,x2,y2);return 2;
   }
   unsigned idle=flushed;forecast_hours(hours);forecast_week(data);lv_refr_now(d);
   assert(flushed==idle); // Identical data must not repaint the fixed graph/forecast bands.
 }
'''
        source=source.replace('WEATHER_FUNCTION',weather.WEEK.replace('id(page_vader)->obj','id(page_vader)'))
        source=source.replace('WEATHER_HOURS_FUNCTION',weather.HOURS.replace('->obj',''))
        source=source.replace('WEATHER_ASSERTS',weather_checks)
        cfg=json.loads(subprocess.run([sys.executable,str(ROOT/'board.py'),'--voice','jarvis_orb.py',
            '--remote','http://example.invalid/assets'],cwd=ROOT/'styles/glass-v2',capture_output=True,text=True,check=True).stdout)
        def find(value):
            if isinstance(value,dict):
                if value.get('id')=='weather_entry':return value
                for child in value.values():
                    found=find(child)
                    if found:return found
            elif isinstance(value,list):
                for child in value:
                    found=find(child)
                    if found:return found
        hint=find(cfg['lvgl'])
        hint_test=r'''
 auto *entry=lv_obj_create(next);lv_obj_remove_style_all(entry);lv_obj_set_pos(entry,520,24);lv_obj_set_size(entry,230,230);
 auto *heading=lv_label_create(entry);lv_label_set_text(heading,"Kungsholmen");lv_obj_set_pos(heading,16,16);
 widgets["weather_entry"]=entry;widgets["w_heading"]=heading;
 lv_obj_add_event_cb(entry,[](lv_event_t*){PRESS_HINT_DOWN},LV_EVENT_PRESSED,nullptr);
 lv_obj_add_event_cb(entry,[](lv_event_t*){PRESS_HINT_UP},LV_EVENT_RELEASED,nullptr);
 lv_refr_now(d);std::memcpy(reference,pixels,sizeof(reference));
 lv_obj_send_event(entry,LV_EVENT_PRESSED,nullptr);lv_refr_now(d);
 int x1=1024,y1=600,x2=0,y2=0;unsigned changes=0;auto *pa=(uint16_t*)reference,*pb=(uint16_t*)pixels;
 for(int y=0;y<600;++y)for(int x=0;x<1024;++x)if(pa[y*1024+x]!=pb[y*1024+x]){
   ++changes;x1=std::min(x1,x);x2=std::max(x2,x);y1=std::min(y1,y);y2=std::max(y2,y);}
 assert(changes&&x2-x1<140&&y2-y1<30); // The card's 230x230 fill must not change.
 lv_obj_send_event(entry,LV_EVENT_PRESS_LOST,nullptr);assert(lv_obj_get_style_text_opa(heading,LV_PART_MAIN)==255);
 lv_obj_send_event(entry,LV_EVENT_PRESSED,nullptr);lv_obj_send_event(entry,LV_EVENT_RELEASED,nullptr);
 assert(lv_obj_get_style_text_opa(heading,LV_PART_MAIN)==255);
'''
        self.assertNotIn('pressed',hint)
        source=source.replace('PRESS_HINT_ASSERTS',hint_test.replace('PRESS_HINT_DOWN',hint['on_press'][0]['lambda'])
            .replace('PRESS_HINT_UP',hint['on_release'][0]['lambda']))
        with tempfile.TemporaryDirectory(prefix='hallpanel-static-ui-') as tmp:
            p=Path(tmp);(p/'t.cpp').write_text(source)
            (p/'esphome/core').mkdir(parents=True)
            (p/'esphome/core/hal.h').write_text('#pragma once\n#include <cstdint>\nnamespace esphome {uint32_t millis();}\n')
            (p/'lv_conf.h').write_text((build/'src/lv_conf.h').read_text().replace('#define LV_USE_LINE 0', '#define LV_USE_LINE 1')
                .replace('#define LV_COLOR_16_SWAP 1','#define LV_COLOR_16_SWAP 0'))
            result=subprocess.run(['cc','-O1','-fsanitize=address,undefined','-DLV_CONF_INCLUDE_SIMPLE',
                '-I'+str(p),'-I'+str(headers[0].parent),'-c',
                str(headers[0].parent/'src/widgets/line/lv_line.c'),'-o',str(p/'line.o')],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            # The SDL simulator's legacy swap mutates a different buffer span on partial/full flush.
            # Compile the actual refresh implementation without it, matching the firmware's RGB565.
            result=subprocess.run(['cc','-O1','-fsanitize=address,undefined','-DLV_CONF_INCLUDE_SIMPLE',
                '-I'+str(p),'-I'+str(headers[0].parent),'-c',
                str(headers[0].parent/'src/core/lv_refr.c'),'-o',str(p/'refresh.o')],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            result=subprocess.run(['c++','-std=c++17','-O1','-fsanitize=address,undefined',
                '-DLV_CONF_INCLUDE_SIMPLE','-I'+str(ROOT),'-I'+str(p),'-I'+str(headers[0].parent),
                str(p/'t.cpp'),str(p/'line.o'),str(p/'refresh.o'),str(libraries[0]),'-o',str(p/'test')],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            result=subprocess.run([str(p/'test')],capture_output=True,text=True,timeout=20)
            self.assertEqual(result.returncode,0,result.stderr)
            optimized=result.stdout
            # Negative control: the same simultaneous updates overflow the dirty list without
            # forecast consolidation. This guards the actual reproduced failure, not source spelling alone.
            control=re.sub(r'ui_update::Batch batch\([^;]+;', '',source)
            self.assertNotEqual(control,source)
            (p/'t.cpp').write_text(control)
            result=subprocess.run(['c++','-std=c++17','-O1','-fsanitize=address,undefined',
                '-DLV_CONF_INCLUDE_SIMPLE','-I'+str(ROOT),'-I'+str(p),'-I'+str(headers[0].parent),
                str(p/'t.cpp'),str(p/'line.o'),str(p/'refresh.o'),str(libraries[0]),'-o',str(p/'control')],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            result=subprocess.run([str(p/'control')],capture_output=True,text=True,timeout=20)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('1024*600',result.stderr)
            result=subprocess.run([str(p/'control'),'reference'],capture_output=True,text=True,timeout=20)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(result.stdout,optimized,'Batch must preserve every final pixel, including unrelated changes')
