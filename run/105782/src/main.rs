#![feature(specialization)]

trait Default {
   type Id;
}

impl<T> Default for T {
   default type Id = T;
}

trait Overlap {
   type Assoc;
}

impl Overlap for u32 {
   type Assoc = usize;
}

impl Overlap for <u32 as Default>::Id {
   type Assoc = Box<usize>;
}

fn main() {}