#![feature(transmutability)]
#![feature(unboxed_closures)]

const fn test() -> impl std::mem::BikeshedIntrinsicFrom() {
    || {}
}

fn main() {}