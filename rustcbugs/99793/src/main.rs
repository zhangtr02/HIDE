const fn f() -> impl Eq { g() }
const fn g() {}
// OK: const fn f() -> impl Eq { g(); }
// OK: const fn f() -> impl Eq {}
// OK: const fn f() -> impl Eq { (g(),).0 }

fn main() {}