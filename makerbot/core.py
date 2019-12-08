#!/usr/bin/env python3
"""
makerbot is a crypto market maker bot that is easy understand and customize

Usage:
  makerbot start <market> [--config=<file>]
  makerbot --version

Options:
  -h --help        Show this screen.
  --version        Show version.
  --config=<file>  Configuration file [default: config.ini].
"""


import time
import logging
import numpy as np
import pandas as pd
from docopt import docopt
from datetime import datetime
from functools import reduce
from getpass import getpass
from nash import NashApi, CurrencyAmount
from decimal import Decimal, getcontext
from .helpers import Order, OrderBookSeries, retry, get_config

__version__ = "0.1.3"
# The maximum precision for amount and prices in Nash is 8, so we set that
getcontext().prec = 8
getcontext().rounding = "ROUND_FLOOR"


def get_obs_dataframe(obs: OrderBookSeries) -> pd.DataFrame:
    """ Compute useful metrics from orderbook series."""
    # midprice
    Pmid = (obs.data['Pask'][:,0] + obs.data['Pbid'][:,0]) / 2.0
    # Imbalance
    I = obs.data['Qbid'][:,0] / (obs.data['Qbid'][:,0] + obs.data['Qask'][:,0])
    Pmicro = (obs.data['Pask'][:,0] * I) + (obs.data['Pbid'][:,0] * (1 - I)) # microprice
    return pd.DataFrame({"Pask": obs.data['Pask'][:,0],
                         "Qask": obs.data['Qask'][:,0],
                         "Pbid": obs.data['Pbid'][:,0],
                         "Qbid": obs.data['Qbid'][:,0],
                         "I": I,
                         "Pmid": Pmid,
                         "Pmicro": Pmicro}, index=obs.t)

def bollinger_bands(df: pd.DataFrame, window: int = 20, k: int = 2) -> pd.DataFrame:
    """ Compute upper, lower values for data on window beteween k sigma
        This funciton is an example how to one could implement metrics using the
        OrderBookSeries and pandas.
    """
    sma = df.Pmid.rolling(window).mean() # Use midprice as fair value
    kstd = df.Pmid.rolling(window).std() * k # Get standard deviation times constant
    upper = sma + kstd
    lower = sma - kstd
    return sma, upper, lower

def funds_in_open_orders(orders) -> Decimal:
    return sum(Decimal(ods.amount_remaining.amount) * Decimal(ods.limit_price.amount) for ods in orders if ods.status == "OPEN")

def get_max_order_funds(orders) -> Decimal:
    """ Get maximum of funds to allocate in a order
    divide by two because bought funds need to be placed for sell
    """
    return (CONFIG["max_funds_in_flight"] - funds_in_open_orders(orders)) / 2

def get_corresponding_sell(market, buy: Order) -> Order:
    """ Build the corresponding sell order for the given buy order."""
    sell_price = buy.price + CONFIG['straddle']
    tmp_order = buy.replace(price = sell_price).replace(buy_or_sell = 'SELL')
    return tmp_order.constrain_price(market)

def place_order(market, order) -> Order:
    """ Place order in NashApi format
    """
    amount = CurrencyAmount(str(order.amount), market.a_unit)
    placed = retry(lambda: api.place_limit_order(market.name,
                                                 amount,
                                                 order.buy_or_sell,
                                                 order.cancellation_policy,
                                                 str(order.price),
                                                 order.allow_taker))
    logger.info("placed limit order {}".format(placed.id))
    return order.replace(id = placed.id)

def get_orders_by_side(orders: list, side: str) -> list:
    """ Get open and pending orders from side."""
    side_orders = (filter(lambda order: order.buy_or_sell == side, orders))
    return [Order.from_api(order) for order in side_orders]

def get_lowest_order(orders: list) -> Order:
    """Get order with lowest price."""
    return reduce(lambda acc, b: acc if acc.price < b.price else b, orders)

def get_active_sell_orders(orders: list):
    sell_orders = get_orders_by_side(orders, 'SELL')
    active = [ order for order in sell_orders if order.status in ['OPEN', 'PENDING'] ]
    return active

def get_last_buy_order(orders: list):
    """Get the current active or last filled order in the list."""
    buy_orders = get_orders_by_side(orders, 'BUY')
    if not len(buy_orders):
        return None
    # Check if more than one buy order running
    active = [ order for order in buy_orders if order.status is ['OPEN', 'PENDING'] ]
    if len(active) > 1:
        raise Exception("More than one active buy order detected!")
    return sorted(buy_orders, key = lambda o: o.placed_at).pop()

def size_order(obs: OrderBookSeries, order: Order) -> Order:
    """ This function is called when a order is being placed and it needs to be
        sized for an amount, needs a order with price and direction
    """
    Q_max = np.longfloat(str(CONFIG['max_funds_in_order'] / order.price))
    last15s = np.argwhere(obs.t > obs.t[-1] - np.timedelta64(15, 's')).flatten()
    side = 'Qask' if order.buy_or_sell == 'BUY' else 'Qbid'
    Q = obs.data[side][last15s]
    amt = min(Q[:,:2].mean(), Q_max)
    return order.replace(amount = amt)

def get_buy_order(obs, df: pd.DataFrame) -> Order:
    """ This function is called to price a order being placed."""
    # Use microprice as reference
    price = Decimal(str(df.Pmicro[-1])) - CONFIG["buy_down_interval"]
    return size_order(obs, Order(price, 0, "BUY"))

def is_equal(lhs, rhs) -> bool:
    """Check if lhs and rhs are equal."""
    diff = abs(Decimal(str(lhs)) - Decimal(str(rhs)))
    return diff < 1e-7

def is_buying(obs: OrderBookSeries) -> bool:
    """Try to determine if market is buying in the last 15 seconds."""
    last15s = np.argwhere(obs.t > obs.t[-1] - np.timedelta64(15, 's')).flatten()
    Qa_top = obs.data['Qask'][last15s][:,:2] # Size of low 2 asks in last 30s
    Qb_top = obs.data['Qbid'][last15s][:,:2] # Size of top 2 bids in last 30s
    Qa_avg = np.average(Qa_top, axis = 1)
    Qb_avg = np.average(Qb_top, axis = 1)
    I = Qb_avg / (Qb_avg + Qa_avg) # Finally Compute imbalance!
    return np.median(I) > 0.5

def should_place_buy(obs: OrderBookSeries) -> bool:
    """ Check if should place order."""
    stable_price = CONFIG["stable_price"]
    max_drop_percentage = CONFIG["max_drop_percentage"]

    min_price = (stable_price * (100 - max_drop_percentage)) / 100
    ob = obs.data[-1] # Current order book view
    fv = Decimal(str((ob['Pask'][0] + ob['Pbid'][0])/2.0)) # midprice as fair value
    if fv < min_price:
        raise Exception("market price is lower than max drop allowed: {}".format(min_price))
    # if market is "buying" our sell has bigger chance to work fast
    return is_buying(obs)

def should_rebuy(sell_orders: dict, buy_order: Order) -> bool:
    """ Decide if should cancel current buy order and issue a new one."""
    low_sell = get_lowest_order(sell_orders)
    eff_straddle = low_sell.price - buy_order.price
    return eff_straddle > CONFIG['straddle'] + CONFIG['buy_down_interval']

def setup_scrum_buy(market, obs, df: pd.DataFrame, buy_order: Order, max_amount: Decimal):
    """ Manage the creation and placement of a scrum buy, handle rebuy and
        cancelation if needed due to market going up.
    """
    buy_1 = get_buy_order(obs, df).constrain(market, max_amount)
    if not buy_order:
        logger.info('No buy order, checking if should place scrum buy.')
        place_order(market, buy_1)
    else:
        is_top_bid = is_equal(buy_order.price, df.Pbid[-1])
        filled = buy_order.amount - buy_order.amount_remaining
        if filled > 0:
            logger.info("Scrum buy filled.")
            api.cancel_order(buy_order.id, market.name)
            scrum_sell = get_corresponding_sell(market, buy_1.replace(amount = filled))
            place_order(market, scrum_sell)
            place_order(market, buy_1)
        # Order has not filled at least 5% and is not the top bid anymore
        elif not is_top_bid:
            logger.info("Scrum not top anymore - rebuy.")
            # Market has not hit the order and it is no longer best ask
            api.cancel_order(buy_order.id, market.name)
            place_order(market, buy_1)
    return

# Mapp the words to logging levels for user convenience
def log_map(levelstr):
    log_levels = {'debug': logging.DEBUG,
                  'info': logging.INFO,
                  'error': logging.ERROR,
                  'warning': logging.WARNING}
    if levelstr in log_levels:
        return log_levels[levelstr]
    raise Exception("Log level in config doesn't match debug|info|error|warning.")

# Map config keywords to its parsing functions
type_map = {'env': str,
            'max_funds_in_flight': Decimal,
            'max_funds_in_order': Decimal,
            'max_drop_percentage': int,
            'min_history_points': int,
            'max_loading_time': int,
            'max_obs_size': int,
            'stable_price': Decimal,
            'buy_down_interval': Decimal,
            'straddle': Decimal,
            'log_to_file': str,
            'log_level': log_map}

def main():
    arguments = docopt(__doc__, version=__version__)
    print("Nash market maker bot, version {}\n".format(__version__))
    login = input('Nash login email: ')
    pwd = getpass('Nash login password: ')
    print("starting ...\n")
    # Setup logger and config
    global CONFIG
    CONFIG = get_config(type_map, arguments['<market>'], arguments['--config'])
    # Stup logging based on configs
    global logger
    logger = logging.getLogger('pymaker')
    logger.setLevel(CONFIG['log_level'])
    # create correct handler
    if CONFIG['log_to_file'] in ['y', 'Y', 'yes', 'Yes', 'YES']:
        handler = logging.FileHandler(r'./makerbot.log')
    else:
        handler = logging.StreamHandler()

    handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s:%(levelname)s: %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    # Loop until Ctrl+C is hit
    try:

        global api
        api = NashApi(environment=CONFIG['env'])
        api.login(login, pwd, None)

        market = api.get_market(CONFIG['market'])
        _, quote = market.name.split('_')
        # We need to add Z since Python is not ISO compliant :(
        start_time = datetime.utcnow().isoformat() + 'Z'

        get_available = lambda: Decimal(api.get_account_balance(quote).available.amount)
        get_orders = lambda: api.list_account_orders(market.name,
                                                     status = ['OPEN', 'PENDING', 'FILLED'],
                                                     range_start = start_time).orders
        logger.info("Enter market maker loop")
        obs = OrderBookSeries.bootstrap(api, CONFIG)
        update_id = 0
        while True:
            # Sleep for 100ms to avoid hitting rate limit
            time.sleep(0.100)
            orderbook = api.get_order_book(market.name)
            # if there has been no changes on the orderbook skip the loop iteration
            if orderbook.update_id == update_id:
                continue
            logger.debug("Updating orderbook series and dataframe.")
            update_id = orderbook.update_id
            obs = obs.update(orderbook, CONFIG['max_obs_size'])
            df = get_obs_dataframe(obs)
            orders = retry(get_orders)
            sell_orders = get_active_sell_orders(orders)
            buy_order = get_last_buy_order(orders)
            # Get maximum amount for a buy order on this round
            max_amount = min(get_max_order_funds(orders), retry(get_available))
            if max_amount < Decimal(market.min_trade_size_b):
                logger.info("Max order size currently lower than market minimum")
                # If funds are unavailable means we need to wait a sell order to fill
                time.sleep(5)
                continue
            if not should_place_buy(obs):
                logger.debug("Skipping placement because market is not buying.")
                continue
            # If we have a live sell order we are market making:
            if not len(sell_orders):
                setup_scrum_buy(market, obs, df, buy_order, max_amount)
                # Give some time after placing scrum buy, the idea is to give change for market
                # volatility to hit it or change price in meaningful way
                time.sleep(5)
            # If we don't have a active buy we nee
            else:
                filled = buy_order.amount - buy_order.amount_remaining
                # If previous buy executed or is executing, place order and move buy
                if filled > 0:
                    logger.info("Previous buy filled. Placing new pair.")
                    api.cancel_order(buy_order.id, market.name)
                    new_buy = get_buy_order(obs, df).constrain(market, max_amount)
                    previous_sell = get_corresponding_sell(market, new_buy.replace(amount = filled))
                    place_order(market, previous_sell)
                    buy_order = place_order(market, new_buy)
                # If straddle becomes to big set re-buy order
                elif should_rebuy(sell_orders, buy_order):
                    is_top_order = is_equal(buy_order.price, df.Pbid[-1])
                    if not is_top_order:
                        logger.info("Straddle too big. Performing rebuy.")
                        api.cancel_order(buy_order.id, market.name)
                        new_buy = get_buy_order(obs, df).constrain_price(market)
                        buy_order = place_order(market, new_buy.replace(amount = buy_order.amount))

    except KeyboardInterrupt:
        logger.warning("Ctrl+C detected, exiting bot.")
    finally:
        logger.info("Canceling bot buy order if any.")
        try:
            api.cancel_order(buy_order.id, market.name)
        except:
            pass
