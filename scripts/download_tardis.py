import argparse
import asyncio
import logging
from pathlib import Path

import pandas as pd
from tardis_dev import datasets

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tardis_downloader")

async def download_and_resample(exchange: str, symbol: str, start_date: str, end_date: str, timeframe: str, out_dir: Path):
    # Convert standard symbol like BTC/USDT to Tardis format (varies by exchange, but typically btcusdt or BTCUSDT)
    # For Bitget spot, Tardis uses uppercase without slash, e.g., BTCUSDT
    tardis_symbol = symbol.replace("/", "").replace("-", "").upper()
    
    # Download from tardis
    logger.info(f"Downloading trades for {exchange} {tardis_symbol} from {start_date} to {end_date}...")
    temp_dir = out_dir / "tardis_temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        await datasets.download(
            exchange=exchange,
            data_types=["trades"],
            from_date=start_date,
            to_date=end_date,
            symbols=[tardis_symbol],
            dir=str(temp_dir)
        )
    except Exception as e:
        logger.error(f"Failed to download data from Tardis: {e}")
        return
    
    # Process downloaded files
    logger.info("Processing downloaded files...")
    files = list(temp_dir.glob(f"{exchange}_trades_*_{tardis_symbol}.csv.gz"))
    if not files:
        logger.error("No files downloaded from Tardis. Check the dates or symbol format.")
        return
        
    dfs = []
    for f in sorted(files):
        df = pd.read_csv(f)
        dfs.append(df)
        
    if not dfs:
        return
        
    # Combine and parse tardis format
    # tardis trades format includes: exchange, symbol, timestamp, local_timestamp, id, side, price, amount
    # timestamp is in microseconds
    full_df = pd.concat(dfs, ignore_index=True)
    full_df["datetime"] = pd.to_datetime(full_df["timestamp"], unit="us")
    full_df.set_index("datetime", inplace=True)
    full_df.sort_index(inplace=True)
    
    # Resample to OHLCV
    tf_pandas = timeframe
    if tf_pandas.endswith('m'):
        tf_pandas = tf_pandas.replace('m', 'min')
    elif tf_pandas.endswith('d'):
        tf_pandas = tf_pandas.replace('d', 'D')
    
    ohlcv = full_df.resample(tf_pandas).agg({
        "price": ["first", "max", "min", "last"],
        "amount": "sum"
    }).dropna()
    
    ohlcv.columns = ["open", "high", "low", "close", "volume"]
    ohlcv["timestamp"] = ohlcv.index.view("int64") // 1_000_000  # convert to ms
    
    cols = ["timestamp", "open", "high", "low", "close", "volume"]
    final_df = ohlcv[cols]
    
    # Save to Intradyne data directory format
    sym_safe = symbol.replace("/", "-")
    exchange_dir = out_dir / exchange
    exchange_dir.mkdir(parents=True, exist_ok=True)
    
    out_path = exchange_dir / f"{sym_safe}_{timeframe}.csv"
    final_df.to_csv(out_path, index=False, float_format='%.8f')
    logger.info(f"Saved {len(final_df)} bars to {out_path}")
    
    # Cleanup temp
    for f in files:
        try:
            f.unlink()
        except OSError:
            pass
    try:
        temp_dir.rmdir()
    except OSError:
        pass

def main():
    parser = argparse.ArgumentParser(description="Download and resample data from Tardis.dev into Intradyne's format")
    parser.add_argument("--exchange", type=str, default="bitget")
    parser.add_argument("--symbols", type=str, required=True, help="Comma separated symbols e.g. BTC/USDT")
    parser.add_argument("--start", type=str, required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", type=str, required=True, help="YYYY-MM-DD")
    parser.add_argument("--timeframe", type=str, default="1m", help="1s, 1m, 5m, etc")
    parser.add_argument("--out-dir", type=str, default="data", help="Output directory (defaults to ./data)")
    
    args = parser.parse_args()
    
    symbols = [s.strip() for s in args.symbols.split(",")]
    out_dir = Path(args.out_dir)
    
    for symbol in symbols:
        asyncio.run(download_and_resample(
            args.exchange,
            symbol,
            args.start,
            args.end,
            args.timeframe,
            out_dir
        ))

if __name__ == "__main__":
    main()
