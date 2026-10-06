//@compile-flags: -Zunpretty=stable-mir --edition=2018
use std::future::ready;
pub fn main() {
    let mut vec: Vec<String> = vec![];

    let closure = async || {
        vec.push(ready(String::from("")).await);
    };
}
