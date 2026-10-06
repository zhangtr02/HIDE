#![feature(never_patterns)]

enum Void {}

fn main() {
    let res_void: Result<bool, Void> = Ok(true);

    for (Ok(mut _x) | Err(!)) in [res_void] {}
}

