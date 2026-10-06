#![feature(generic_const_exprs)]

mod assert {
    use std::mem::{Assume, BikeshedIntrinsicFrom};

    pub fn is_transmutable<
        Src,
        Dst,
        Context,
        const ASSUME_ALIGNMENT: bool,
        const ASSUME_LIFETIMES: bool,
        const ASSUME_SAFETY: bool,
        const ASSUME_VALIDITY: bool,
    >()
    where
        Dst: BikeshedIntrinsicFrom<
            Src,
            Context,
            { from_options(ASSUME_ALIGNMENT, ASSUME_LIFETIMES, ASSUME_SAFETY, ASSUME_VALIDITY) }
        >,
    {}

    const fn from_options(
        ,
        ,
        ,
        ,
    ) -> Assume {}
}

fn test() {
    struct Context;
    #[repr(C)] struct Src;
    #[repr(C)] struct Dst;
     
    false(); 
    assert::is_transmutable::<Src, Dst, Context, false, false, { true }, false>(); 
     
}

fn main() {}