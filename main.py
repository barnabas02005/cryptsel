from keep_alive import keep_alive
keep_alive()
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

                order_side = 'sell' if side == 'short' else 'buy'   # Adjust as needed for 'long'/'short'
                order_price = mark_price
                double_notional = notional * 2
                order_amount = double_notional / mark_price
                order_amount = round_to_sig_figs(order_amount, sig_digits)

                print("Double Margin: ", double_notional)
                print("New Order Amount: ", order_amount)

                try:
                    # You can configure additional parameters here
                    order_params = {
                        'reduceOnly': False,
                        'marginType': 'isolated',
                        'posSide': 'Long' if side == 'long' else 'Short',
                    }
                    order = exchange.create_order(
                        symbol=symbol,
                        type='market',
                        side=order_side,
                        amount=order_amount,
                        params=order_params
                    )
                    print(f"✅ Re-entry order placed: {order_side} {order_amount} @ {order_price}")
                except Exception as e:
                    print(f"❌ Error placing re-entry order: {e}")
            else:
                print("✅ Not close enough to liquidation for re-entry.")
        else:
            print(f"No open{symbol} positions found.")
    except ccxt.ExchangeError as e:
        print(f"Exchange error: {e}")
    except KeyError as ke:
        print(f"Missing key: {ke}")

    # Fetch recent orders
    try:
        orders = exchange.fetchOrders(symbol)
        if orders:
            print("\nRecent Trades:")
            for order in orders:
                print(f"Symbol: {order['symbol']}")
                print(f"Type: {order['type']}")
                print(f"Side: {order['side']}")
                print(f"Price: {order['price']}")
                print(f"Amount: {order['amount']}")
                print(f"Cost: {order['cost']}")
                print(f"Filled: {order['filled']}")
                print("------")
        else:
            print(f"No trade {symbol} history found.")
    except ccxt.ExchangeError as e:
        print(f"Error fetching orders: {e}")

    time.sleep(1)

TRAILING_FOLDER = "trailProfit"
TRAILING_ORDER_FOLDER = "tradeOrder"
# Ensure folder exists
os.makedirs(TRAILING_FOLDER, exist_ok=True)
os.makedirs(TRAILING_ORDER_FOLDER, exist_ok=True)

def safe_filename(symbol):
    return symbol.replace('/', '_').replace(':', '_')

def load_trailing_data(symbol):
    filename = f"{safe_filename(symbol)}.json"
    filepath = os.path.join(TRAILING_FOLDER, filename)
    if os.path.exists(filepath):
        with open(filepath, "r") as f:
            return json.load(f)
    return None

def save_trailing_data(symbol, data):
    filename = f"{safe_filename(symbol)}.json"
    filepath = os.path.join(TRAILING_FOLDER, filename)
    with open(filepath, "w") as f:
        json.dump(data, f, indent=4)

def delete_trailing_data(symbol):
    filename = f"{safe_filename(symbol)}.json"
    filepath = os.path.join(TRAILING_FOLDER, filename)
    if os.path.exists(filepath):
        os.remove(filepath)
        print(f"Deleted trailing data for {symbol}")
        return True
    else:
        print(f"No trailing data found to delete for {symbol}")
        return False

def save_stop_order_id(symbol, order_id):
    filepath = os.path.join(TRAILING_ORDER_FOLDER, f"{safe_filename(symbol)}_stop_id.json")
    with open(filepath, "w") as f:
        json.dump({"order_id": order_id}, f)

def check_stop_order_filled(exchange, symbol):
    filepath = os.path.join(TRAILING_ORDER_FOLDER, f"{safe_filename(symbol)}_stop_id.json")
    if not os.path.exists(filepath):
        return False

    with open(filepath, "r") as f:
        data = json.load(f)
    order_id = data.get("order_id")

    try:
        order = exchange.fetch_order(order_id, symbol)
        if order['status'] in ['closed', 'filled']:
            print(f"✅ Stop-loss order {order_id} for {symbol} was filled.")
            os.remove(filepath)
            delete_trailing_data(symbol)
            return True
        elif order['status'] == 'canceled':
            print(f"⚠️ Stop-loss order {order_id} was canceled.")
            os.remove(filepath)
        else:
            print(f"⌛ Stop-loss order {order_id} still open.")
    except Exception as e:
        print(f"❌ Error checking stop order: {e}")
    return False

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

    # Load or initialize trailing data for this symbol
    trailing_data = load_trailing_data(symbol) or {
        'threshold': 0.10,  # +30%
        'stop_loss': 0.010   # +20%
    } # {'NXCP"USDT"USDT': {'threshold': 0.3, 'stop_loss': 0.2}}

    threshold = trailing_data['threshold']
    stop_loss = trailing_data['stop_loss']

    if side == 'long':
        change = (mark_price - entry_price) / entry_price
    else:
        change = (entry_price - mark_price) / entry_price

    profit_distance = change * leverage  # stays in decimal form

    print("distance entry - last price (for profit): ", profit_distance)

    if profit_distance >= threshold:
        print(f"📈 Hello! {side.capitalize()} position on {symbol} is up {round(change * 100, 2)}%")

        if side == 'long':
            new_stop_price = mark_price - (mark_price * stop_loss)

            if new_stop_price <= entry_price:
                print(f"New stop loss @ {new_stop_price} price is less than entry price @ {entry_price}")
                return
        else:
            new_stop_price = mark_price + (mark_price * stop_loss)
            if new_stop_price >= entry_price:
                print(f"New stop loss @ {new_stop_price} price is greater than entry price @ {entry_price}")
                return

        print(f"🔄 Moving stop-loss to {round(stop_loss * 100, 2)}%, at price {new_stop_price:.4f}")

        try:
            open_orders = exchange.fetch_open_orders(symbol)
            for order in open_orders:
                if order['type'] == 'stop' and order.get('reduceOnly', True):
                    exchange.cancel_order(order['id'], symbol)
                    print(f"✅ Cancelled old stop-loss for {symbol}")
        except Exception as e:
            print(f"⚠️ Failed to cancel stop-loss: {e}")

        try:
            trigger_direction = 1 if side == 'long' else 2  # down for long, up for shor
            order = exchange.create_order(
                symbol=symbol,
                type='stop',
                side='sell' if side == 'long' else 'buy',
                amount=contracts,
                price=None,  # Market order when triggered
                params={
                    'stopPx': new_stop_price,
                    'triggerType': 'ByLastPrice',  # or 'ByMarkPrice'/'ByIndexPrice'
                    'triggerDirection': trigger_direction,
                    'positionIdx': 1 if side == 'long' else 2,
                    'posSide': 'Long' if side == 'long' else 'Short',
                    'closeOnTrigger': True,
                    'reduceOnly': True,
                    'timeInForce': 'GoodTillCancel',
                }
            )
            print(f"✅ Placed stop-loss at {new_stop_price:.4f} for {symbol}")
            # Update and save trailing info per symbol
            trailing_data['stop_loss'] = stop_loss
            trailing_data['threshold'] = threshold + breath_threshold
            save_trailing_data(symbol, trailing_data)
            save_stop_order_id(symbol, order['id'])
        except Exception as e:
            print(f"❌ Failed to set stop-loss: {e}")


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
        open_positions = [pos for pos in positionst if pos.get('contracts', 0) > 0 or pos.get('size', 0) > 0]

        for pos in open_positions:
            trailing_stop_logic(exchange, pos, 0.10, 0.10)
            check_stop_order_filled(exchange, pos['symbol'])
            monitor_position_and_reenter(exchange, pos['symbol'], pos)
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
