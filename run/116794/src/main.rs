fn opaque<'a: 'a>(_: &'a str) -> *mut impl Sized {
    &mut ()
}

fn main() {
    let x = opaque(&String::new()); //~ ERROR temporary value dropped while borrowed
    drop(x);
}