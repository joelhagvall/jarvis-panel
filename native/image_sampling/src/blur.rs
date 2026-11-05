//! Separable edge-padded convolution with the renderer's original tap order.
//!
//! Each pass splits its output rows into contiguous bands on up to two
//! threads. Every output value still sees the same taps in the same order with
//! the same per-tap rounding, so the result is bit-identical to one thread.

use std::num::NonZeroUsize;
use std::thread;

/// Smaller images run on the calling thread; spawning would cost more.
const MIN_THREADED_ROWS: usize = 64;
/// On Jarvis (4 cores) four bands gave the same wall time as two for 0.3 s more CPU per picture.
const MAX_THREADS: usize = 2;

pub(crate) trait BlurValue: Copy + Default + Send + Sync {
    fn accumulate(&mut self, source: Self, weight: f64);
}

impl BlurValue for f64 {
    #[inline]
    fn accumulate(&mut self, source: Self, weight: f64) {
        *self += weight * source;
    }
}

impl BlurValue for f32 {
    #[inline]
    fn accumulate(&mut self, source: Self, weight: f64) {
        // NumPy multiplies by a float64 kernel, adds in float64, then rounds
        // back to the float32 accumulator after EACH tap, not after the sum.
        *self = (f64::from(*self) + weight * f64::from(source)) as f32;
    }
}

pub(crate) fn length(
    width: usize,
    height: usize,
    channels: usize,
    taps: usize,
    element_size: usize,
) -> Option<usize> {
    let length = width.checked_mul(height)?.checked_mul(channels)?;
    if width == 0
        || height == 0
        || channels == 0
        || taps == 0
        || taps.is_multiple_of(2)
        || length > isize::MAX as usize / element_size
        || taps > isize::MAX as usize / size_of::<f64>()
    {
        return None;
    }
    Some(length)
}

fn thread_count(height: usize) -> usize {
    if height < MIN_THREADED_ROWS {
        return 1;
    }
    thread::available_parallelism()
        .map_or(1, NonZeroUsize::get)
        .min(MAX_THREADS)
}

pub(crate) fn apply<T: BlurValue>(
    source: &[T],
    dimensions: (usize, usize, usize),
    weights: &[f64],
    scratch: &mut [T],
    output: &mut [T],
) -> i32 {
    let threads = thread_count(dimensions.1);
    apply_threads(source, dimensions, weights, scratch, output, threads)
}

fn apply_threads<T: BlurValue>(
    source: &[T],
    dimensions: (usize, usize, usize),
    weights: &[f64],
    scratch: &mut [T],
    output: &mut [T],
    threads: usize,
) -> i32 {
    let (width, height, channels) = dimensions;
    let Some(count) = length(width, height, channels, weights.len(), size_of::<T>()) else {
        return 1;
    };
    if source.len() != count
        || scratch.len() != count
        || output.len() != count
        || weights.iter().any(|w| !w.is_finite())
    {
        return 1;
    }
    let row_size = width * channels;
    let rows_per_band = height.div_ceil(threads.clamp(1, height));
    in_bands(scratch, row_size, rows_per_band, |first_row, band| {
        vertical(source, dimensions, weights, first_row, band);
    });
    let scratch = &*scratch;
    in_bands(output, row_size, rows_per_band, |first_row, band| {
        horizontal(scratch, dimensions, weights, first_row, band);
    });
    0
}

/// Runs `work(first_row, band)` over disjoint row bands, one per thread.
///
/// If the OS refuses a thread, the bands it would have owned went with the
/// dropped closure, so the whole buffer is recomputed on this thread. Both
/// passes overwrite their band from zero, so the redo gives the same bits.
fn in_bands<T: Send>(
    buffer: &mut [T],
    row_size: usize,
    rows_per_band: usize,
    work: impl Fn(usize, &mut [T]) + Sync,
) {
    let band_len = rows_per_band * row_size;
    if buffer.len() <= band_len {
        work(0, buffer);
        return;
    }
    let spawned_all = thread::scope(|scope| {
        let mut bands = buffer.chunks_mut(band_len);
        let first = bands.next().expect("buffer is longer than one band");
        let work = &work;
        let mut spawned_all = true;
        for (index, band) in bands.enumerate() {
            let first_row = (index + 1) * rows_per_band;
            spawned_all &= thread::Builder::new()
                .spawn_scoped(scope, move || work(first_row, band))
                .is_ok();
        }
        work(0, first);
        spawned_all
    });
    if !spawned_all {
        work(0, buffer);
    }
}

/// Vertical pass for output rows `first_row..`, written into `band`.
fn vertical<T: BlurValue>(
    source: &[T],
    (width, height, channels): (usize, usize, usize),
    weights: &[f64],
    first_row: usize,
    band: &mut [T],
) {
    let radius = weights.len() / 2;
    let row_size = width * channels;
    let last_row = isize::try_from(height - 1).expect("validated array fits in isize");
    let radius_signed = isize::try_from(radius).expect("validated kernel fits in isize");
    band.fill(T::default());
    // Row-outer, tap-inner keeps one accumulator row in L1 instead of
    // streaming the whole buffer through DRAM once per tap. Each value's own
    // tap order is unchanged, so the bits are too.
    for (local, target) in band.chunks_exact_mut(row_size).enumerate() {
        let y = isize::try_from(first_row + local).expect("validated array fits in isize");
        for (tap, &weight) in weights.iter().enumerate() {
            let offset =
                isize::try_from(tap).expect("validated kernel fits in isize") - radius_signed;
            let sy = y.saturating_add(offset).clamp(0, last_row);
            let sy = usize::try_from(sy).expect("clamped source row is nonnegative");
            let input = &source[sy * row_size..(sy + 1) * row_size];
            for (target, &input) in target.iter_mut().zip(input) {
                target.accumulate(input, weight);
            }
        }
    }
}

/// Horizontal pass for output rows `first_row..`, written into `band`.
fn horizontal<T: BlurValue>(
    scratch: &[T],
    (width, _, channels): (usize, usize, usize),
    weights: &[f64],
    first_row: usize,
    band: &mut [T],
) {
    let radius = weights.len() / 2;
    let row_size = width * channels;
    let radius_signed = isize::try_from(radius).expect("validated kernel fits in isize");
    let start = first_row * row_size;
    let rows = &scratch[start..start + band.len()];
    band.fill(T::default());
    for (input, target) in rows
        .chunks_exact(row_size)
        .zip(band.chunks_exact_mut(row_size))
    {
        for (tap, &weight) in weights.iter().enumerate() {
            let offset =
                isize::try_from(tap).expect("validated kernel fits in isize") - radius_signed;
            let left = radius.saturating_sub(tap).min(width);
            let right = width.saturating_sub(tap.saturating_sub(radius));
            for pixel in target[..left * channels].chunks_exact_mut(channels) {
                for (value, &edge) in pixel.iter_mut().zip(&input[..channels]) {
                    value.accumulate(edge, weight);
                }
            }
            if left < right {
                let start = left
                    .checked_add_signed(offset)
                    .expect("middle source coordinate is in the row");
                let input = &input[start * channels..(start + right - left) * channels];
                let target = &mut target[left * channels..right * channels];
                for (value, &input) in target.iter_mut().zip(input) {
                    value.accumulate(input, weight);
                }
            }
            for pixel in target[right * channels..].chunks_exact_mut(channels) {
                for (value, &edge) in pixel.iter_mut().zip(&input[row_size - channels..]) {
                    value.accumulate(edge, weight);
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn edge_padding_matches_a_hand_computed_two_by_two_blur() {
        let mut scratch = [0.0; 4];
        let mut output = [0.0; 4];
        assert_eq!(
            apply(
                &[1.0, 2.0, 3.0, 4.0],
                (2, 2, 1),
                &[0.25, 0.5, 0.25],
                &mut scratch,
                &mut output
            ),
            0
        );
        assert_eq!(output, [1.75, 2.25, 2.75, 3.25]);
    }

    #[test]
    fn rejects_invalid_shapes_and_nonfinite_weights_without_writing() {
        let mut scratch = [42.0; 4];
        let mut output = [43.0; 4];
        assert_eq!(
            apply(&[1.0; 4], (2, 2, 1), &[1.0, 2.0], &mut scratch, &mut output),
            1
        );
        assert_eq!(
            apply(&[1.0; 4], (2, 2, 1), &[f64::NAN], &mut scratch, &mut output),
            1
        );
        assert_eq!(scratch, [42.0; 4]);
        assert_eq!(output, [43.0; 4]);
        assert_eq!(length(usize::MAX, 2, 3, 3, 8), None);
    }

    #[test]
    fn small_images_stay_on_one_thread_and_large_ones_respect_the_cap() {
        assert_eq!(thread_count(1), 1);
        assert_eq!(thread_count(MIN_THREADED_ROWS - 1), 1);
        assert!((1..=MAX_THREADS).contains(&thread_count(600)));
    }

    /// Deterministic values in [0, 1) without a dependency.
    fn noise(count: usize, seed: u64) -> Vec<f64> {
        let mut state = seed;
        (0..count)
            .map(|_| {
                state = state
                    .wrapping_mul(6_364_136_223_846_793_005)
                    .wrapping_add(1_442_695_040_888_963_407);
                (state >> 11) as f64 / (1u64 << 53) as f64
            })
            .collect()
    }

    fn gaussian(sigma: f64) -> Vec<f64> {
        let radius = (3.0 * sigma + 0.5) as i64;
        let raw: Vec<f64> = (-radius..=radius)
            .map(|x| (-(x * x) as f64 / (2.0 * sigma * sigma)).exp())
            .collect();
        let sum: f64 = raw.iter().sum();
        raw.iter().map(|w| w / sum).collect()
    }

    fn run<T: BlurValue>(
        source: &[T],
        dimensions: (usize, usize, usize),
        weights: &[f64],
        threads: usize,
    ) -> Vec<T> {
        let mut scratch = vec![T::default(); source.len()];
        let mut output = vec![T::default(); source.len()];
        let status = apply_threads(
            source,
            dimensions,
            weights,
            &mut scratch,
            &mut output,
            threads,
        );
        assert_eq!(status, 0);
        output
    }

    #[test]
    fn threaded_bands_are_bit_identical_to_one_thread() {
        let shapes = [
            (17, 1, 3),
            (1, 1, 1),
            (5, 1, 1),
            (1, 9, 3),
            (7, 13, 1),
            (13, 7, 3),
            (33, 65, 3),
            (64, 64, 1),
            (31, 200, 3),
            (101, 97, 1),
        ];
        let kernels = [vec![1.0], gaussian(0.8), gaussian(3.0), gaussian(12.0)];
        for (seed, &(width, height, channels)) in shapes.iter().enumerate() {
            let values = noise(width * height * channels, seed as u64 + 1);
            let narrow: Vec<f32> = values.iter().map(|&v| v as f32).collect();
            for weights in &kernels {
                let dims = (width, height, channels);
                let wide_one = run(&values, dims, weights, 1);
                let narrow_one = run(&narrow, dims, weights, 1);
                for threads in [2, 3, 4, 5, 8, height + 3] {
                    let wide = run(&values, dims, weights, threads);
                    let narrow_many = run(&narrow, dims, weights, threads);
                    let context = (dims, weights.len(), threads);
                    assert!(
                        wide.iter()
                            .zip(&wide_one)
                            .all(|(a, b)| a.to_bits() == b.to_bits()),
                        "f64 {context:?}"
                    );
                    assert!(
                        narrow_many
                            .iter()
                            .zip(&narrow_one)
                            .all(|(a, b)| a.to_bits() == b.to_bits()),
                        "f32 {context:?}"
                    );
                }
            }
        }
    }

    #[test]
    fn public_entry_matches_one_thread_on_a_panel_sized_image() {
        let dims = (128, 75, 3);
        let values: Vec<f32> = noise(128 * 75 * 3, 99).iter().map(|&v| v as f32).collect();
        let weights = gaussian(28.0);
        let mut scratch = vec![0.0f32; values.len()];
        let mut output = vec![0.0f32; values.len()];
        assert_eq!(apply(&values, dims, &weights, &mut scratch, &mut output), 0);
        let one = run(&values, dims, &weights, 1);
        assert!(
            output
                .iter()
                .zip(&one)
                .all(|(a, b)| a.to_bits() == b.to_bits())
        );
    }
}
