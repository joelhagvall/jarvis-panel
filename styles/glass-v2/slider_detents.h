#pragma once

namespace slider_detents {
// Stops are percentages (0, 5, 10, ...), not offsets from the slider's minimum.
// Keep existing safety minima: day brightness 5%, night brightness 1%, volume 0%.
constexpr int nearest(int value, int minimum, int maximum = 100) {
  value = value < minimum ? minimum : value > maximum ? maximum : value;
  const int rounded = ((value + 2) / 5) * 5;
  return rounded < minimum ? minimum : rounded > maximum ? maximum : rounded;
}
}
