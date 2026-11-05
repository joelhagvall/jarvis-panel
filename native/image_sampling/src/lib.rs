//! Bilinear RGB sampler used by the panel background renderer on Jarvis.

mod blur;
mod qoi;

#[unsafe(no_mangle)]
pub extern "C" fn hallpanel_blur_abi() -> u32 {
    1
}

macro_rules! blur_export {
    ($name:ident, $value:ty) => {
        /// Edge-padded separable convolution using caller-owned work buffers.
        ///
        /// # Safety
        /// Source, scratch and output must each point to width*height*channels
        /// aligned values of the declared type, and weights to taps readable
        /// aligned f64s. Scratch/output must be mutually disjoint and disjoint
        /// from both inputs. All regions must remain valid during this call;
        /// inputs must not be changed concurrently.
        #[unsafe(no_mangle)]
        pub unsafe extern "C" fn $name(
            source: *const $value,
            width: usize,
            height: usize,
            channels: usize,
            weights: *const f64,
            taps: usize,
            scratch: *mut $value,
            output: *mut $value,
        ) -> i32 {
            let Some(count) = blur::length(width, height, channels, taps, size_of::<$value>())
            else {
                return 1;
            };
            if source.is_null() || weights.is_null() || scratch.is_null() || output.is_null() {
                return 1;
            }
            // SAFETY: Caller promises aligned readable source for this checked length.
            let source = unsafe { std::slice::from_raw_parts(source, count) };
            // SAFETY: Caller promises aligned readable weights for this checked length.
            let weights = unsafe { std::slice::from_raw_parts(weights, taps) };
            // SAFETY: Caller promises aligned writable scratch disjoint from every other region.
            let scratch = unsafe { std::slice::from_raw_parts_mut(scratch, count) };
            // SAFETY: Caller promises aligned writable output disjoint from every other region.
            let output = unsafe { std::slice::from_raw_parts_mut(output, count) };
            blur::apply(source, (width, height, channels), weights, scratch, output)
        }
    };
}

blur_export!(hallpanel_blur_f32, f32);
blur_export!(hallpanel_blur_f64, f64);

#[unsafe(no_mangle)]
pub extern "C" fn hallpanel_qoi_abi() -> u32 {
    1
}

/// Encode RGB bytes as QOI into a caller-owned buffer.
///
/// # Safety
/// `pixels` must be readable for `input_len` bytes and `output` writable for
/// `capacity` bytes, both for this call. `written` must be a valid aligned
/// writable usize. All three regions must be disjoint, and inputs must not be
/// changed concurrently. Rejected dimensions/lengths are checked before access.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn hallpanel_qoi_rgb(
    pixels: *const u8,
    input_len: usize,
    width: u32,
    height: u32,
    output: *mut u8,
    capacity: usize,
    written: *mut usize,
) -> i32 {
    let Some((expected, maximum)) = qoi::sizes(width, height) else {
        return 1;
    };
    if input_len != expected
        || capacity < maximum
        || capacity > isize::MAX as usize
        || pixels.is_null()
        || output.is_null()
        || written.is_null()
    {
        return 1;
    }
    // SAFETY: Caller promises initialized readable bytes for the validated length.
    let pixels = unsafe { std::slice::from_raw_parts(pixels, input_len) };
    // SAFETY: Caller promises this writable buffer, disjoint from all inputs.
    let output = unsafe { std::slice::from_raw_parts_mut(output, capacity) };
    match qoi::encode_rgb(pixels, width, height, output) {
        Ok(length) => {
            // SAFETY: Caller promises an aligned writable usize, disjoint from buffers.
            unsafe {
                written.write(length);
            }
            0
        }
        Err(code) => code,
    }
}

/// ABI version checked by the Python binding before passing any buffers.
#[unsafe(no_mangle)]
pub extern "C" fn hallpanel_sampling_abi() -> u32 {
    1
}

fn sample_pixel(image: &[f32], width: usize, height: usize, sx: f64, sy: f64) -> [f64; 3] {
    let sx = sx.clamp(0.0, width as f64 - 1.001);
    let sy = sy.clamp(0.0, height as f64 - 1.001);
    // Coordinates have been clamped to the interior of the image. Truncating
    // their nonnegative floors gives the four valid neighbouring pixel indices.
    let x = sx.floor() as usize;
    let y = sy.floor() as usize;
    let fx = sx - x as f64;
    let fy = sy - y as f64;
    std::array::from_fn(|channel| {
        let at = |xx, yy| f64::from(image[(yy * width + xx) * 3 + channel]);
        let a = at(x, y) * (1.0 - fx) + at(x + 1, y) * fx;
        let b = at(x, y + 1) * (1.0 - fx) + at(x + 1, y + 1) * fx;
        a * (1.0 - fy) + b * fy
    })
}

/// Sample an RGB float32 image into an RGB float64 output buffer.
///
/// # Safety
/// For this call, `image` must point to `width * height * 3` initialized f32s;
/// `sx` and `sy` to `count` initialized f64s, and `output` to `count * 3`
/// writable f64s. All pointers must be aligned and valid for their lengths.
/// The output must not alias any input, and no input may be mutated concurrently.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn hallpanel_sample(
    image: *const f32,
    width: usize,
    height: usize,
    sx: *const f64,
    sy: *const f64,
    count: usize,
    output: *mut f64,
) -> i32 {
    let Some(image_len) = width.checked_mul(height).and_then(|n| n.checked_mul(3)) else {
        return 1;
    };
    let Some(output_len) = count.checked_mul(3) else {
        return 1;
    };
    if width < 2
        || height < 2
        || count == 0
        || image.is_null()
        || sx.is_null()
        || sy.is_null()
        || output.is_null()
        || image_len > isize::MAX as usize / size_of::<f32>()
        || output_len > isize::MAX as usize / size_of::<f64>()
    {
        return 1;
    }
    // SAFETY: The caller promises aligned, initialized, readable image storage
    // with this checked length for the duration of the call.
    let image = unsafe { std::slice::from_raw_parts(image, image_len) };
    // SAFETY: The caller promises aligned, readable coordinate buffers of count f64s.
    let sx = unsafe { std::slice::from_raw_parts(sx, count) };
    // SAFETY: The same coordinate-buffer contract applies to sy.
    let sy = unsafe { std::slice::from_raw_parts(sy, count) };
    // SAFETY: The caller promises a writable output of this checked length,
    // disjoint from every input and with no concurrent access.
    let output = unsafe { std::slice::from_raw_parts_mut(output, output_len) };
    if sx.iter().chain(sy).any(|v| !v.is_finite()) {
        return 2;
    }
    for ((&x, &y), pixel) in sx.iter().zip(sy).zip(output.as_chunks_mut::<3>().0) {
        pixel.copy_from_slice(&sample_pixel(image, width, height, x, y));
    }
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn interpolates_all_channels_and_clamps_outside_coordinates() {
        let image = [0.0, 0.1, 0.2, 1.0, 0.5, 0.4, 0.5, 0.3, 0.6, 0.75, 0.9, 0.8];
        let centre = sample_pixel(&image, 2, 2, 0.5, 0.5);
        for (actual, expected) in centre.into_iter().zip([0.5625, 0.45, 0.5]) {
            assert!((actual - expected).abs() < 1e-7);
        }
        assert_eq!(
            sample_pixel(&image, 2, 2, -100.0, -10.0),
            [0.0, f64::from(0.1_f32), f64::from(0.2_f32)]
        );
        for (actual, expected) in sample_pixel(&image, 2, 2, 2.0, 2.0)
            .into_iter()
            .zip(sample_pixel(&image, 2, 2, 0.999, 0.999))
        {
            assert!((actual - expected).abs() < 1e-12);
        }
    }

    #[test]
    fn rejects_invalid_dimensions_before_dereferencing() {
        // SAFETY: Null pointers are rejected before any dereference.
        let result = unsafe {
            hallpanel_sample(
                std::ptr::null(),
                1,
                2,
                std::ptr::null(),
                std::ptr::null(),
                1,
                std::ptr::null_mut(),
            )
        };
        assert_eq!(result, 1);
    }

    #[test]
    fn rejects_nonfinite_coordinates_without_writing_output() {
        let image = [0.0_f32; 12];
        let sx = [f64::NAN];
        let sy = [0.5];
        let mut output = [42.0; 3];
        // SAFETY: All buffers have the required lengths and remain alive,
        // aligned and disjoint throughout this call.
        let result = unsafe {
            hallpanel_sample(
                image.as_ptr(),
                2,
                2,
                sx.as_ptr(),
                sy.as_ptr(),
                1,
                output.as_mut_ptr(),
            )
        };
        assert_eq!(result, 2);
        assert_eq!(output, [42.0; 3]);
    }
}
