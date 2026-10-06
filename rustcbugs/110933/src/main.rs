#![feature(associated_const_equality)]

trait Trait {
    const ASSOC: usize;
}

fn foo<T: Trait<ASSOC = {
    let a = 10_usize;
    let b: &'_ usize = &a;
    *b
}>>() {
    
}

fn main() {}