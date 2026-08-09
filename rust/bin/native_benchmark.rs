use serde_json::Value;
use socketioxide::{
    SocketIo,
    extract::{AckSender, Data, SocketRef},
};

async fn on_connect(socket: SocketRef) {
    socket.on("echo", async |Data::<Value>(data), ack: AckSender| {
        ack.send(&data).ok();
    });
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let port = std::env::var("FASTSIO_BENCH_PORT")
        .unwrap_or_else(|_| "8765".to_owned())
        .parse::<u16>()?;
    let (layer, io) = SocketIo::new_layer();
    io.ns("/", on_connect);

    let app = axum::Router::new().layer(layer);
    let listener = tokio::net::TcpListener::bind(("127.0.0.1", port)).await?;
    axum::serve(listener, app).await?;
    Ok(())
}
