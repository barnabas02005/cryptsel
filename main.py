import ccxt
import os
import time
import json
import math
import schedule
import traceback

from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv('API_KEY')
secret = os.getenv('SECRET')

def count_sig_digits(precision):
    # Count digits after decimal point if it's a fraction
    if precision < 1:
        return abs(int(round(math.log10(precision))))
    else:
        return 1  # Treat whole numbers like 1, 10, 100 as 1 sig digit
    
def round_to_sig_figs(num, sig_figs):
    if num == 0:
        return 0
    return round(num, sig_figs - int(math.floor(math.log10(abs(num)))) - 1)

def monitor_position_and_reenter(exchange, symbol, position):
    try:
        if position:
            liquidation_price = float(position.get('liquidationPrice') or 0)
            entry_price = float(position.get('entryPrice') or 0)
            mark_price = float(position.get('markPrice') or 0)
            contracts = float(position.get('contracts') or 0)
            leverage = float(position.get("leverage") or 1)
            notional = float(position.get('notional') or 0)
            # print("Notation: ", notional)
            precision_val = exchange.markets[symbol]['precision']['amount']
            sig_digits = count_sig_digits(precision_val)
            side = position.get('side').lower()  # typically 'long' or 'short'
            
            if not liquidation_price or not entry_price or not mark_price:
                return  # Skip if essential data is missing

            # Calculate how far the price has moved toward liquidation.
            if side == 'long':
                closeness = 1 - (abs(mark_price - liquidation_price) / abs(entry_price - liquidation_price))
            else:  # short
                closeness = 1 - (abs(mark_price - liquidation_price) / abs(entry_price - liquidation_price))

            print(f"\n--- {symbol} ---")
            print(f"Side: {side}")
            print(f"Entry Price: {entry_price}")
            print(f"Mark Price: {mark_price}")
            print(f"Liquidation Price: {liquidation_price}")
            print(f"Closeness to Liquidation: {closeness * 100:.2f}%")

            # Trigger re-entry logic if close to liquidation
            if closeness >= 0.8:
                print("⚠️  Mark price is 80% close to liquidation! Considering re-entry...")
            
                order_side = 'sell' if side == 'short' else 'buy'
                order_price = mark_price
                double_notional = notional * 2
                order_amount = double_notional / mark_price
                order_amount = round_to_sig_figs(order_amount, sig_digits)
            
                print("Double Margin: ", double_notional)
                print("New Order Amount: ", order_amount)
            
                try:
                    # Set isolated margin explicitly before placing the order
                    exchange.set_margin_mode('isolated', symbol)
            
                    # Determine posSide correctly
                    pos_side = 'Long' if order_side == 'buy' else 'Short'
            
                    order_params = {
                        'reduceOnly': False,
                        'posSide': pos_side,
                        'marginMode': 'isolated'  # Optional, reinforces intent
                    }
            
                    order = exchange.create_order(
                        symbol=symbol,
                        type='market',
                        side=order_side,
                        amount=order_amount,
                        params=order_params
                    )
                    print(f"✅ Re-entry order placed: {order_side} {order_amount} @ {order_price}")
            
                except ccxt.BaseError as e:
                    print(f"❌ Error placing re-entry order: {e}")

            else:
                print("✅ Not close enough to liquidation for re-entry.")
        else:
            print(f"No open{symbol} positions found.")
    except ccxt.ExchangeError as e:
        print(f"Exchange error: {e}")
    except KeyError as ke:
        print(f"Missing key: {ke}")

    # # Fetch recent orders
    # try:
    #     orders = exchange.fetchOrders(symbol)
    #     if orders:
    #         print("\nRecent Trades:")
    #         for order in orders:
    #             print(f"Symbol: {order['symbol']}")
    #             print(f"Type: {order['type']}")
    #             print(f"Side: {order['side']}")
    #             print(f"Price: {order['price']}")
    #             print(f"Amount: {order['amount']}")
    #             print(f"Cost: {order['cost']}")
    #             print(f"Filled: {order['filled']}")
    #             print("------")
    #     else:
    #         print(f"No trade {symbol} history found.")
    # except ccxt.ExchangeError as e:
    #     print(f"Error fetching orders: {e}")

    time.sleep(1)

TRAILING_FOLDER = "trailProfit"
TRAILING_ORDER_FOLDER = "tradeOrder"

# Ensure base folders exist
os.makedirs(os.path.join(TRAILING_FOLDER, "buy"), exist_ok=True)
os.makedirs(os.path.join(TRAILING_FOLDER, "sell"), exist_ok=True)
os.makedirs(TRAILING_ORDER_FOLDER, exist_ok=True)


def safe_filename(symbol):
    return symbol.replace('/', '_').replace(':', '_')

def load_trailing_data(symbol, side):
    filename = f"{safe_filename(symbol)}.json"
    subfolder = 'buy' if side == 'long' else 'sell'
    filepath = os.path.join(TRAILING_FOLDER, subfolder, filename)
    if os.path.exists(filepath):
        with open(filepath, "r") as f:
            return json.load(f)
    return None


def save_trailing_data(symbol, data, side):
    filename = f"{safe_filename(symbol)}.json"
    subfolder = 'buy' if side == 'long' else 'sell'
    filepath = os.path.join(TRAILING_FOLDER, subfolder, filename)
    data['side'] = 'buy' if side == 'long' else 'sell'
    with open(filepath, "w") as f:
        json.dump(data, f, indent=4)


def delete_trailing_data(symbol):
    filename = f"{safe_filename(symbol)}.json"
    deleted = False
    for subfolder in ['buy', 'sell']:
        filepath = os.path.join(TRAILING_FOLDER, subfolder, filename)
        if os.path.exists(filepath):
            os.remove(filepath)
            print(f"🗑️ Deleted trailing data for {symbol} from {subfolder} folder")
            deleted = True
    if not deleted:
        print(f"⚠️ No trailing data found to delete for {symbol}")
    return deleted


def reset_trailing_data(symbol=None):
    if symbol:
        filepath = os.path.join(TRAILING_FOLDER, f"{symbol.replace('/', '_')}.json")
        if os.path.exists(filepath):
            os.remove(filepath)
            print(f"🧹 Trailing data reset for {symbol}. File deleted.")
        else:
            print(f"🧹 No trailing data file found for {symbol}. Nothing to delete.")
    else:
        for filename in os.listdir(TRAILING_FOLDER):
            filepath = os.path.join(TRAILING_FOLDER, filename)
            os.remove(filepath)
        print("🧹 All trailing data reset. All files deleted.")

# The main trailing stop logic now loads/saves per symbol
def trailing_stop_logic(exchange, position, breath_stop, breath_threshold):
    symbol = position.get('symbol')
    entry_price = float(position.get('entryPrice') or 0)
    mark_price = float(position.get('markPrice') or 0)
    side = position.get('side', '').lower()
    leverage = float(position.get("leverage") or 1)
    contracts = float(position.get('contracts') or 0)

    if not entry_price or not mark_price or side not in ['long', 'short'] or contracts <= 0:
        return


    trailing_data = load_trailing_data(symbol, side) or {
        'threshold': 0.10,
        'profit_target_distance': 0.04
    }

    threshold = trailing_data['threshold']
    profit_target_distance = trailing_data['profit_target_distance']
    order_id = trailing_data.get('orderId')

    change = (mark_price - entry_price) / entry_price if side == 'long' else (entry_price - mark_price) / entry_price
    profit_distance = change * leverage

    print("Leverage: ", leverage)
    unrealized_pnl = (mark_price - entry_price) * contracts if side == 'long' else (entry_price - mark_price) * contracts
    realized_pnl = float(position["info"].get('curTermRealisedPnlRv') or 0)
    addUnreRea = unrealized_pnl + realized_pnl
    # # Round to 4 significant figures inline
    # unrealized_pnl_rounded = round_to_sig_figs(unrealized_pnl, 4)
    # realized_pnl_rounded = round_to_sig_figs(realized_pnl, 4)

    print("Unrealized PnL:", unrealized_pnl)
    print("Realized PnL:", realized_pnl)
    print(f"Add unrpnl and reapnl: {addUnreRea}")
    print("distance entry - last price (for profit):", profit_distance)
    
    if addUnreRea <= 0:
        if order_id:
            try:
                # Try canceling without posSide param first (one-way mode)
                exchange.cancel_order(order_id, symbol=symbol)
                print(f"❌ Canceled previous stop-loss {order_id} without posSide")
            except Exception as e:
                error_msg = str(e)
                # Check if error is related to inconsistent position mode
                if "TE_ERR_INCONSISTENT_POS_MODE" in error_msg:
                    try:
                        # Retry with posSide param (hedge mode)
                        params = {'posSide': 'Long' if side == 'long' else 'Short'}
                        exchange.cancel_order(order_id, symbol=symbol, params=params)
                        print(f"❌ Canceled previous stop-loss {order_id} with posSide param")
                    except Exception as e2:
                        print(f"⚠️ Failed to cancel stop-loss even with posSide: {e2}")
                else:
                    print(f"⚠️ Failed to cancel stop-loss: {e}")
                    
        delete_trailing_data(symbol)
        return

    if profit_distance >= threshold:
        print(f"📈 Hello! {side.capitalize()} position on {symbol} is up {round(change * 100, 2)}%")
        new_stop_price = entry_price * (1 + profit_target_distance / leverage) if side == 'long' else entry_price * (1 - profit_target_distance / leverage)

        if (side == 'long' and new_stop_price <= entry_price) or (side == 'short' and new_stop_price >= entry_price):
            print(f"New stop loss @ {new_stop_price} is not valid relative to entry price @ {entry_price}")
            return

        print(f"🔄 Moving stop-loss to {round(profit_target_distance * 100, 2)}%, at price {new_stop_price:.4f}")

        # ✅ Cancel old order if it exists
        if order_id:
            try:
                # Try canceling without posSide param first (one-way mode)
                exchange.cancel_order(order_id, symbol=symbol)
                print(f"❌ Canceled previous stop-loss {order_id} without posSide")
            except Exception as e:
                error_msg = str(e)
                # Check if error is related to inconsistent position mode
                if "TE_ERR_INCONSISTENT_POS_MODE" in error_msg:
                    try:
                        # Retry with posSide param (hedge mode)
                        params = {'posSide': 'Long' if side == 'long' else 'Short'}
                        exchange.cancel_order(order_id, symbol=symbol, params=params)
                        print(f"❌ Canceled previous stop-loss {order_id} with posSide param")
                    except Exception as e2:
                        print(f"⚠️ Failed to cancel stop-loss even with posSide: {e2}")
                else:
                    print(f"⚠️ Failed to cancel stop-loss: {e}")

        order_created = False

        # ✅ Try creating stop-loss in hedge mode
        try:
            order = exchange.create_order(
                symbol=symbol,
                type='stop',
                side='sell' if side == 'long' else 'buy',
                amount=contracts,
                price=None,
                params={
                    'stopPx': new_stop_price,
                    'triggerType': 'ByLastPrice',
                    'triggerDirection': 1 if side == 'long' else 2,  # 🔥 This line is required
                    'positionIdx': 1 if side == 'long' else 2,
                    'posSide': 'Long' if side == 'long' else 'Short',
                    'closeOnTrigger': True,
                    'reduceOnly': True,
                    'timeInForce': 'GoodTillCancel',
                }
            )
            print(f"✅ Placed new stop-loss at {new_stop_price:.4f} for {symbol}")
            order_created = True
        except Exception as e:
            print(f"⚠️ Hedge mode failed: {e} — retrying in one-way mode")

        # ✅ Fallback: one-way mode
        if not order_created:
            try:
                order = exchange.create_order(
                    symbol=symbol,
                    type='stop',
                    side='sell' if side == 'long' else 'buy',
                    amount=contracts,
                    price=None,
                    params={
                        'stopPx': new_stop_price,
                        'triggerType': 'ByLastPrice',
                        'triggerDirection': 1 if side == 'long' else 2,  # 🔥 This line is required
                        'reduceOnly': True,
                        'closeOnTrigger': True,
                        'timeInForce': 'GoodTillCancel',
                    }
                )
                print(f"✅ Placed stop-loss in one-way mode at {new_stop_price:.4f} for {symbol}")
                order_created = True
            except Exception as e2:
                print(f"❌ Failed again (one-way mode): {e2}")
                return

        # ✅ Save updated trailing data
        if order_created:
            trailing_data['orderId'] = order['id']
            trailing_data['profit_target_distance'] = profit_target_distance + breath_threshold
            trailing_data['threshold'] = threshold + breath_threshold
            trailing_data['order_updated'] = True
            save_trailing_data(symbol, trailing_data, side)




def cleanup_closed_trailing_files(exchange, symbols):
    try:
        positionst = exchange.fetch_positions(symbols=symbols)
    except Exception as e:
        print("❌ Failed to fetch positions for cleanup:", e)
        return

    active = {
        ('buy' if pos.get('side', '').lower() == 'long' else 'sell', f"{safe_filename(pos.get('symbol'))}.json")
        for pos in positionst
        if pos.get('contracts', 0) > 0 and pos.get('side', '').lower() in ['long', 'short']
    }

    for subfolder in ['buy', 'sell']:
        path = os.path.join(TRAILING_FOLDER, subfolder)
        try:
            for fname in os.listdir(path):
                if (subfolder, fname) not in active:
                    os.remove(os.path.join(path, fname))
                    print(f"🧹 Deleted stale trailing file: {subfolder}/{fname}")
        except FileNotFoundError:
            continue




# MAIN
def main():
    try:
        # Initialize exchange in isolated margin mode if needed.
        # Some exchanges require setting margin mode on initialization or via separate endpoints.
        exchange = ccxt.phemex({
            'apiKey': api_key,
            'secret': secret,
            # Additional configuration may be needed depending on your exchange setup.
        })
        
        # Step 1: Get all markets
        markets = exchange.load_markets()
        
        all_symbols = [symbol for symbol in markets if ":USDT" in symbol]
        positionst = exchange.fetch_positions(symbols=all_symbols)
        usdt_balance = exchange.fetch_balance({'type': 'swap'})['USDT']['total']
        print("USDT Balance: ", usdt_balance)
        for pos in positionst:
            trailing_stop_logic(exchange, pos, 0.10, 0.10)
            if pos.get('contracts', 0) > 0 or pos.get('size', 0) > 0:
                monitor_position_and_reenter(exchange, pos['symbol'], pos)
                
        # 🧹 Clean up closed positions' trailing files
        cleanup_closed_trailing_files(exchange, all_symbols)
    except Exception as e:
        print("Error inside job:")
        traceback.print_exc()


schedule.every(10).seconds.do(main)

# ✅ Outer loop handles everything
while True:
    try:
        schedule.run_pending()
        time.sleep(1)
    except Exception as e:
        print("Scheduler crashed:")
        traceback.print_exc()
        print("Retrying in 10 seconds...")
        time.sleep(10)
