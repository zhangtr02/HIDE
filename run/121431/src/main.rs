fn bug<T>() -> impl CallbackMarker<
    Item = [(); {
               |_: &mut ()| x;
               4
           }],
> {
}

fn main() {}