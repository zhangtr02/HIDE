#![feature(decl_macro)]

macro emit($($m:ident)*) {$(
    // Randomize `def_path_hash` by defining them under a module with
    // different names
    pub mod $m {
        pub trait Tr {
            type Next: Tr;
        }

        pub fn hoge<const N: usize, T: Tr>() {
            inner::<N, T>();
        }

        #[inline(always)]
        fn inner<const N: usize, T: Tr>() {
            inner::<N, T::Next>();
        }
    }
)*}

// Increase the chance of triggering the bug
emit!(
    m00 m01 m02 m03 m04 m05 m06 m07 m08 m09
    m10 m11 m12 m13 m14 m15 m16 m17 m18 m19
);

fn main() {}