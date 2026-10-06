#![feature(pin_ergonomics)]

use std::pin::Pin;

pub fn foo() {
    let _: Pin<Box<()>> = Box::pin(());
}

fn main() {}