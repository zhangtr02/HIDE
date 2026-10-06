#![feature(explicit_tail_calls)]
fn cut(x: &mut ()) {
    if let Some(_) = Some(Box::new(())) {
        become cut(x);
    }
}