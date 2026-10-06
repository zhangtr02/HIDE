trait Bar<const N: BB> {}
trait BB = Bar<{ 2 + 1 }>;

fn main() {}