//! RGB-only QOI encoder. Opcode choices retain Pillow's existing wire bytes.

pub(crate) fn sizes(width: u32, height: u32) -> Option<(usize, usize)> {
    let pixels = usize::try_from(width)
        .ok()?
        .checked_mul(usize::try_from(height).ok()?)?;
    if pixels == 0 {
        return None;
    }
    let input = pixels.checked_mul(3)?;
    let capacity = pixels.checked_mul(4)?.checked_add(22)?;
    if input > isize::MAX as usize || capacity > isize::MAX as usize {
        return None;
    }
    Some((input, capacity))
}

fn signed_delta(delta: u8) -> i16 {
    i16::from(i8::from_ne_bytes([delta]))
}

fn opcode(value: i16) -> u8 {
    u8::try_from(value).expect("validated qoi opcode components fit in one byte")
}

pub(crate) fn encode_rgb(
    pixels: &[u8],
    width: u32,
    height: u32,
    output: &mut [u8],
) -> Result<usize, i32> {
    let (input_len, capacity) = sizes(width, height).ok_or(1)?;
    if pixels.len() != input_len || output.len() < capacity {
        return Err(1);
    }
    output[..4].copy_from_slice(b"qoif");
    output[4..8].copy_from_slice(&width.to_be_bytes());
    output[8..12].copy_from_slice(&height.to_be_bytes());
    output[12..14].copy_from_slice(&[3, 1]); // RGB; retain Pillow's default colorspace tag.
    let mut at = 14;
    let mut previous = [0_u8; 3];
    let mut seen = [None; 64];
    let mut run = 0_u8;
    for pixel in pixels.as_chunks::<3>().0 {
        if *pixel == previous {
            run += 1;
            if run == 62 {
                output[at] = 0xc0 | (run - 1);
                at += 1;
                run = 0;
            }
        } else {
            if run != 0 {
                output[at] = 0xc0 | (run - 1);
                at += 1;
                run = 0;
            }
            let hash = (usize::from(pixel[0]) * 3
                + usize::from(pixel[1]) * 5
                + usize::from(pixel[2]) * 7
                + 255 * 11)
                % 64;
            if seen[hash] == Some(*pixel) {
                output[at] = u8::try_from(hash).expect("qoi hash is below 64");
                at += 1;
            } else {
                seen[hash] = Some(*pixel);
                let dr8 = pixel[0].wrapping_sub(previous[0]);
                let dg8 = pixel[1].wrapping_sub(previous[1]);
                let db8 = pixel[2].wrapping_sub(previous[2]);
                let (dr, dg, db) = (signed_delta(dr8), signed_delta(dg8), signed_delta(db8));
                let (drg, dbg) = (
                    signed_delta(dr8.wrapping_sub(dg8)),
                    signed_delta(db8.wrapping_sub(dg8)),
                );
                if (-2..2).contains(&dr) && (-2..2).contains(&dg) && (-2..2).contains(&db) {
                    output[at] =
                        0x40 | (opcode(dr + 2) << 4) | (opcode(dg + 2) << 2) | opcode(db + 2);
                    at += 1;
                } else if (-32..32).contains(&dg)
                    && (-8..8).contains(&drg)
                    && (-8..8).contains(&dbg)
                {
                    output[at] = 0x80 | opcode(dg + 32);
                    output[at + 1] = (opcode(drg + 8) << 4) | opcode(dbg + 8);
                    at += 2;
                } else {
                    output[at] = 0xfe;
                    output[at + 1..at + 4].copy_from_slice(pixel);
                    at += 4;
                }
            }
        }
        previous = *pixel;
    }
    if run != 0 {
        output[at] = 0xc0 | (run - 1);
        at += 1;
    }
    output[at..at + 8].copy_from_slice(&[0, 0, 0, 0, 0, 0, 0, 1]);
    Ok(at + 8)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn black_runs_split_at_62_pixels_and_keep_the_header_and_padding() {
        let pixels = [0_u8; 3 * 63];
        let mut output = vec![0; sizes(63, 1).unwrap().1];
        let len = encode_rgb(&pixels, 63, 1, &mut output).unwrap();
        assert_eq!(&output[..14], b"qoif\0\0\0?\0\0\0\x01\x03\x01");
        assert_eq!(&output[14..len], &[0xfd, 0xc0, 0, 0, 0, 0, 0, 0, 0, 1]);
    }

    #[test]
    fn rejects_invalid_lengths_and_capacities_without_writing() {
        let mut output = [42; 26];
        assert_eq!(encode_rgb(&[0, 0], 1, 1, &mut output), Err(1));
        assert_eq!(encode_rgb(&[0, 0, 0], 1, 1, &mut output[..25]), Err(1));
        assert_eq!(output, [42; 26]);
        assert_eq!(sizes(0, 1), None);
        assert_eq!(sizes(u32::MAX, u32::MAX), None);
    }
}
