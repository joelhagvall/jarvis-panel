#pragma once

#include "esphome/core/defines.h"
#ifdef USE_RUNTIME_IMAGE_QOI

#include <memory>

#include "esphome/components/display/display_color_utils.h"
#include "image_decoder.h"
#include "runtime_image.h"

namespace esphome::runtime_image {

/**
 * @brief Image decoder specialization for QOI images.
 */
class QoiDecoder : public ImageDecoder {
 public:
  /**
   * @brief Construct a new QOI decoder object.
   *
   * @param image The RuntimeImage to decode the stream into.
   */
  QoiDecoder(RuntimeImage *image) : ImageDecoder(image, QOI) {}

  void reset() override;
  int HOT decode(uint8_t *buffer, size_t size) override;

  bool is_finished() const override {
    if (this->bits_per_pixel_ == 0) {
      // header not yet received, so dimensions not yet determined
      return false;
    }
    // QOI is finished when we've decoded all pixel data
    return this->paint_index_ >= static_cast<size_t>(this->width_ * this->height_);
  }

 protected:
  std::unique_ptr<Color[]> color_table_;
  size_t current_index_{0};
  size_t paint_index_{0};
  size_t width_{0};
  size_t height_{0};
  Color last_pixel_{0, 0, 0, 255};  // QOI spec defines initial previous pixel as opaque black
  uint16_t bits_per_pixel_{0};
  /// Hallpanel: direct RGB565 target at 1:1 scale (RuntimeImage::rgb565_pixels), else nullptr.
  uint16_t *pixels_{nullptr};
  /// One decoded pixel at paint_index_: stored directly, or through ImageDecoder::draw.
  inline void put_(const Color &color) {
    if (this->pixels_ != nullptr) {
      this->pixels_[this->paint_index_] = display::ColorUtil::color_to_565(color);
    } else {
      this->draw(this->paint_index_ % this->width_, this->paint_index_ / this->width_, 1, 1, color);
    }
    this->paint_index_++;
  }
};

}  // namespace esphome::runtime_image

#endif  // USE_RUNTIME_IMAGE_QOI
