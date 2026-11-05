#include "async_artwork.h"
#include <algorithm>
#include <array>
#include <cstring>
#include "esp_heap_caps.h"
#include "esp_http_client.h"
#include "esphome/core/hal.h"
#include "esphome/core/log.h"
namespace esphome::async_artwork {
AsyncArtwork *instance{};
static constexpr const char *TAG="artwork";
AsyncArtwork::AsyncArtwork(const std::string &url)
    : PollingComponent(4294967295U), image::Image(nullptr,288,288,image::IMAGE_TYPE_RGB565,image::TRANSPARENCY_OPAQUE),
      decoder_(runtime_image::QOI,image::IMAGE_TYPE_RGB565,image::TRANSPARENCY_OPAQUE,nullptr,false,288,288) {
  prefix_=url.substr(0,url.rfind('/')+1);set_url(url);instance=this;
}
void AsyncArtwork::set_url(const std::string &url) {
  valid_=artwork_contract::key_url(url,prefix_);
  if(valid_)url_=url;
}
void AsyncArtwork::setup() {
  for(auto &p:buffers_)p=static_cast<uint16_t *>(heap_caps_malloc((artwork_contract::PIXELS+144*144)*2,MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT));
  // Optional: without it "föregående" downloads again, as before.
  behind_=static_cast<uint16_t *>(heap_caps_malloc((artwork_contract::PIXELS+144*144)*2,MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT));
  if(!buffers_[0]||!buffers_[1] || // Stack in PSRAM (docs/memory.md): 6 kB of internal RAM back; the worker never runs with the cache off.
     xTaskCreatePinnedToCoreWithCaps([](void *p){static_cast<AsyncArtwork *>(p)->worker();},
       // Priority 0, raised to 1 while Spelas nu is shown (set_foreground): at 0 it shared time slices with core 0's
       // idle task and a cover after next took 1240 ms (668 ms at boot); at 2, level with the record worker, the
       // record dropped frames (vinyl-60-20261029/flow-6); at 1 always, the voice orb did (the soak gate).
       "artwork",6144,this,0,&task_,0,MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT)!=pdPASS) {
    ESP_LOGE(TAG,"Artwork worker allocation failed");mark_failed();
  }
}
void AsyncArtwork::update() {
  if((!busy_||prefetching_)&&behind_&&!behind_url_.empty()&&behind_url_==url_&&handoff_.shown>=0) {
    // The cover shown before ("föregående"): it changes places with the shown one, which becomes behind. No slot a
    // download writes is touched, so it never waits for the next cover's download ahead (2026-10-30: 270 ms).
    const int shown=handoff_.shown;
    std::swap(buffers_[shown],behind_);std::swap(picture_identity_[shown],behind_identity_);std::swap(behind_url_,shown_url_);
    data_start_=reinterpret_cast<const uint8_t *>(buffers_[shown]);++requests_;++behind_hits_;
    ESP_LOGI(TAG,"Artwork ready behind");finished_.call();return;
  }
  if(busy_&&prefetching_){pending_update_=true;return;}   // runs as soon as the download ahead is done
  if(busy_){ESP_LOGW(TAG,"Artwork request already active; input was not replayed");return;}
  if(!valid_||!task_){++errors_;error_.call();return;}
  busy_=true;target_=handoff_.target();++requests_;
  if(ahead_.hit(url_,target_)) {
    // Downloaded ahead: publish that buffer on the next loop, without a request to Home Assistant.
    ahead_.forget();++ahead_hits_;ESP_LOGI(TAG,"Artwork ready ahead");job_url_=url_;
    done_.store(1,std::memory_order_release);return;
  }
  ahead_.forget();job_url_=url_;job_ahead_=false;
  requested_.store(true,std::memory_order_release);xTaskNotifyGive(task_);
}
void AsyncArtwork::prefetch(const std::string &url) {
  // An unused cover ahead stays until an update takes or drops it: right after a skip HA names the track after the
  // new one before the new one's key arrives, and replacing it then threw the new track's cover away (2026-10-30).
  if(busy_||!task_||!artwork_contract::key_url(url,prefix_)||ahead_.slot>=0||url==url_)return;
  busy_=prefetching_=job_ahead_=true;target_=handoff_.target();job_url_=url;ahead_.forget();++aheads_;
  requested_.store(true,std::memory_order_release);xTaskNotifyGive(task_);
}
void AsyncArtwork::worker() {
  for(;;) {
    ulTaskNotifyTake(pdTRUE,portMAX_DELAY);
    if(!requested_.exchange(false,std::memory_order_acquire))continue;
    const uint32_t start=micros();const bool ok=download();worker_us_.store(micros()-start);
    if(!ok)++errors_;
    done_.store(ok?1:-1,std::memory_order_release);
  }
}
// Bounded 4 KiB reads: a quarter of the HTTP/transport round trips of 1 KiB for the 288 px QOI (~140 KB).
constexpr size_t READ=4096;
bool AsyncArtwork::download() {
  esp_http_client_config_t config{};config.url=job_url_.c_str();config.timeout_ms=5000;
  // The client's own buffer stays 1 KiB of internal RAM; reads beyond it go straight into the PSRAM buffer below.
  config.buffer_size=1024;config.buffer_size_tx=512;config.disable_auto_redirect=true;
  // The URL is confined to this component's configured HA media-art origin/path. No tokens/credentials.
  struct Client {esp_http_client_handle_t p{};~Client(){if(p){esp_http_client_close(p);esp_http_client_cleanup(p);}}} http;
  http.p=esp_http_client_init(&config);
  if(!http.p){ESP_LOGW(TAG,"Artwork HTTP allocation failed");return false;}
  const uint32_t began=millis();
  if(esp_http_client_open(http.p,0)!=ESP_OK || esp_http_client_fetch_headers(http.p)<0 ||
     esp_http_client_get_status_code(http.p)!=200 || esp_http_client_get_content_length(http.p)>int64_t(artwork_contract::MAX_BYTES)) {
    ESP_LOGW(TAG,"Artwork HTTP refused or unavailable");return false;
  }
  if(!decoder_.set_external_buffer(reinterpret_cast<uint8_t *>(buffers_[target_]),288,288))return false;
  // In PSRAM, not internal RAM: internal free fell to ~7 KB at its lowest on 2026-10-29 and a candidate aborted on
  // core 0. Allocated once; only this one worker task downloads.
  static uint8_t *buffer_data=static_cast<uint8_t *>(heap_caps_malloc(READ+16,MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT));
  if(!buffer_data){ESP_LOGW(TAG,"Artwork read buffer allocation failed");return false;}
  struct View {uint8_t *p;uint8_t *data(){return p;} size_t size()const{return READ+16;}} buffer{buffer_data};
  std::memset(buffer_data,0,READ+16);size_t used=0,total=0;
  bool started=false,failed=false;
  for(;;) {
    if(millis()-began>8000){ESP_LOGW(TAG,"Artwork deadline exceeded");failed=true;break;}
    const int n=esp_http_client_read(http.p,reinterpret_cast<char *>(buffer.data()+used),std::min<size_t>(READ,buffer.size()-used));
    if(n<0){failed=true;break;}if(n==0)break;
    used+=n;total+=n;
    if(total>artwork_contract::MAX_BYTES){failed=true;break;}
    if(!started) {
      if(used<14)continue;
      if(!artwork_contract::header(buffer.data(),used)){ESP_LOGW(TAG,"Artwork dimensions/format refused");failed=true;break;}
      if(!decoder_.begin_decode(0,runtime_image::QOI)){failed=true;break;}started=true;
    }
    if(!artwork_contract::feed(decoder_,buffer.data(),used)){failed=true;break;}
  }
  const uint8_t end[8]={0,0,0,0,0,0,0,1};
  const bool ok=!failed&&started&&decoder_.is_decode_finished()&&used==8&&!std::memcmp(buffer.data(),end,8);
  if(ok) {
    decoder_.end_decode();
    auto *src=buffers_[target_],*small=src+artwork_contract::PIXELS;
    picture_identity_[target_]=artwork_contract::picture_identity(reinterpret_cast<const uint8_t *>(src),artwork_contract::PIXELS*2);
    for(int y=0;y<144;++y)for(int x=0;x<144;++x) {
      const int i=y*2*288+x*2;
      const uint16_t p[4]={src[i],src[i+1],src[i+288],src[i+289]};
      uint32_t r=0,g=0,b=0;for(auto v:p){r+=v>>11;g+=(v>>5)&63;b+=v&31;}
      small[y*144+x]=uint16_t(((r/4)<<11)|((g/4)<<5)|(b/4));
    }
  } else decoder_.release();
  bytes_.store(total);
  ESP_LOGI(TAG,"Artwork %s%s: %u bytes in %u ms on worker",ok?"ready":"failed",job_ahead_?" ahead":"",
           unsigned(total),unsigned(millis()-began));
  return ok;
}
void AsyncArtwork::loop() {
  const int result=done_.exchange(0,std::memory_order_acquire);if(!result)return;
  if(prefetching_) {
    prefetching_=busy_=false;
    if(result>0){ahead_.keep(job_url_,target_);ahead_ready_.call();}
    if(pending_update_){pending_update_=false;update();}
    return;
  }
  const uint32_t start=micros();
  if(result>0){
    const int left=handoff_.shown;
    handoff_.publish(target_);data_start_=reinterpret_cast<const uint8_t *>(buffers_[target_]);
    // The cover just left goes behind; the buffer it swaps with becomes the free one (ahead_ is empty here).
    if(left>=0&&behind_){std::swap(buffers_[left],behind_);std::swap(picture_identity_[left],behind_identity_);behind_url_=shown_url_;}
    shown_url_=job_url_;
  }
  busy_=false;
  if(result>0)finished_.call();else error_.call();
  publish_us_.store(micros()-start);
}
std::function<std::string()> AsyncArtwork::deferred_status() const {
  const auto requests=requests_.load(),errors=errors_.load(),bytes=bytes_.load(),work=worker_us_.load(),pub=publish_us_.load(),
             ahead=aheads_.load(),hits=ahead_hits_.load(),back=behind_hits_.load();
  return [requests,errors,bytes,work,pub,ahead,hits,back](){return ",\"artwork\":{\"requests\":"+std::to_string(requests)+
    ",\"errors\":"+std::to_string(errors)+",\"bytes\":"+std::to_string(bytes)+",\"worker_us\":"+std::to_string(work)+
    ",\"publish_us\":"+std::to_string(pub)+",\"ahead\":"+std::to_string(ahead)+",\"ahead_hits\":"+std::to_string(hits)+",\"behind_hits\":"+std::to_string(back)+"}";};
}
}  // namespace esphome::async_artwork
