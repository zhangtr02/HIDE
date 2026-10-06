trait SomethingSomething: Send {}

struct Wrapper<T> {
    something: Box<dyn SomethingSomething>,
    other: T,
}

trait HasSend {
    type IsSend<T: Send>: Send;
}

impl<T> HasSend for Wrapper<T> {
    type IsSend<S: Send> = Wrapper<S>;
}

fn main() {}