fn opaque<F>(_: F) -> impl Iterator { b"".iter() }
fn assert_static<T: 'static>(_: T) {}

fn generic_fn<T>() {
    // proving `<OpaqueTy<type_of(async {})> as Iterator>::Item: 'static`
    // somehow requires `T: 'static`.
    assert_static(opaque(async {}).next());
    //~^ the associated type `<impl Iterator as Iterator>::Item` may not live long enough
    assert_static(opaque(|| {}).next());
    //~^ the associated type `<impl Iterator as Iterator>::Item` may not live long enough
}

fn main() {}