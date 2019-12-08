import copy
import time
import configparser
import numpy as np
from typing import NamedTuple
from decimal import Decimal
from datetime import datetime
from types import MappingProxyType


def get_config(type_map, market: str, configfile="config.ini"):
    """Creates a immutable proxy for a configuration a dict from config file
    """
    parser = configparser.ConfigParser()
    parser.read(configfile)

    config_mut = parser.defaults()
    config_mut.update(parser[market])
    config_mut.update(market = market)

    for setting in type_map:
        try:
            config_mut[setting] = type_map[setting](config_mut[setting])
        except KeyError:
            raise Exception("Missing {} setting in the config file".format(setting))
        except ValueError:
            raise Exception("The {} setting should be of type {}".format(setting, type_map[setting]))

    return MappingProxyType(config_mut)

def retry(func, max_tries = 16, timeout = 0.1):
    """Helper to retry functions and preserve exceptions, handy for network calls."""
    acm = 0
    while True:
        try:
            return func()
        except Exception as exception:        
            if acm > max_tries:
                raise exception
            time.sleep(timeout * (2 ** acm))
            acm += 1

def parse_positive_decimal(value, label):
    val = Decimal(str(value))
    if val < 0:
        raise Exception("{} must be positive.".format(label))
    return val

def parse_str_option(string, options):
    option = string.strip().upper()
    if option not in options:
        raise Exception("Option must be one of {}".format(option, options))
    return option

class Order:
    """Immutable wrapper for Orders from the API that can save you from mistakes."""

    def __parse_buy_or_sell__(buy_or_sell):
        return parse_str_option(buy_or_sell, ('BUY', 'SELL'))

    def __parse_id__(id):
        return str(int(id))

    def __parse_type__(type):
        return parse_str_option(type, ('LIMIT', 'MARKET', 'STOP_LIMIT', 'STOP_MARKET'))

    def __parse_status__(status):
        return parse_str_option(status, ('CANCELLED', 'FILLED', 'OPEN', 'PENDING'))

    def __parse_policy__(cancellation_policy):
        policies = ('FILL_OR_KILL', 'GOOD_TIL_CANCELLED', 'GOOD_TIL_TIME', 'IMMEDIATE_OR_CANCEL')
        return parse_str_option(cancellation_policy, policies)

    parse_map = {'price': lambda val: parse_positive_decimal(val, 'price'),
                 'amount': lambda val: parse_positive_decimal(val, 'amount'),
                 'buy_or_sell': __parse_buy_or_sell__,
                 'amount_remaining': lambda val: parse_positive_decimal(val, 'amount_remaining'),
                 'id': __parse_id__,
                 'type': __parse_type__,
                 'cancellation_policy': __parse_policy__,
                 'allow_taker': bool,
                 'placed_at': lambda val: val,
                 'status': __parse_status__}

    def __init__(self,
                 price: Decimal,
                 amount: Decimal,
                 buy_or_sell: str,
                 amount_remaining: Decimal = -1,
                 id: str = '-1',
                 type: str = 'LIMIT',
                 cancellation_policy: str = 'GOOD_TIL_CANCELLED',
                 allow_taker: bool = True,
                 placed_at: datetime = None,
                 status: str = 'PENDING'):

        amount_remaining = Decimal(amount_remaining if amount_remaining != -1 else amount)

        object.__setattr__(self, 'price', self.parse_map['price'](price))
        object.__setattr__(self, 'amount', self.parse_map['price'](amount))
        object.__setattr__(self, 'buy_or_sell', self.parse_map['buy_or_sell'](buy_or_sell))
        object.__setattr__(self, 'amount_remaining', self.parse_map['amount_remaining'](amount_remaining))
        object.__setattr__(self, 'id', self.parse_map['id'](id))
        object.__setattr__(self, 'type', self.parse_map['type'](type))
        object.__setattr__(self, 'cancellation_policy', self.parse_map['cancellation_policy'](cancellation_policy))
        object.__setattr__(self, 'allow_taker', self.parse_map['allow_taker'](allow_taker))
        object.__setattr__(self, 'placed_at', self.parse_map['placed_at'](placed_at))
        object.__setattr__(self, 'status', self.parse_map['status'](status))

    def __setattr__(self, *args):
        raise TypeError

    def __delattr__(self, *args):
        raise TypeError

    def replace(self, **kwargs):
        new_order = copy.deepcopy(self)
        for key in kwargs:
            object.__setattr__(new_order, key, self.parse_map[key](kwargs[key]))
        return new_order

    def constrain_price(self, market):
        """ Constrain order price to market settings
        """
        prc = self.price.quantize(Decimal(market.min_trade_increment_b), rounding='ROUND_DOWN')
        return self.replace(price = prc)

    def constrain_amount(self, market, max_amount: Decimal):
        """ Constrain order amount to user and market settings
        """
        amt = min(self.amount, max_amount).quantize(Decimal(market.min_trade_increment), rounding='ROUND_DOWN')
        return self.replace(amount = amt)

    def constrain(self, market, max_amount: Decimal):
        """ Takes an order and constrain it to market and user settings
            order: is the order to be formated
            mkt: is the market object from API call
            max_amount: is the constraint on amount
        """
        return self.constrain_price(market).constrain_amount(market, max_amount)

    def from_api(order):
        """Format the API object to a more usefull format with Decimal."""
        return Order(price = Decimal(order.limit_price.amount),
                     amount = Decimal(order.amount.amount),
                     buy_or_sell = order.buy_or_sell,
                     amount_remaining = Decimal(order.amount_remaining.amount),
                     id = order.id,
                     type = order.type,
                     cancellation_policy = order.cancellation_policy,
                     placed_at = order.placed_at,
                     status = order.status)

class OrderBookSeries(NamedTuple):
    """A fixed size data ordered 3d numpy array and a 1d time array
    return (time[max_obs_size], data[max_obs_size, 25, 4])
    data = (ask prices, askamount, bids, bids_amount)
    """
    t: np.ndarray = np.zeros((0), dtype="datetime64[ns]")
    data: np.ndarray = np.zeros((0, 25), dtype=[('Pask', '<f16'), ('Qask', '<f16'), ('Pbid', '<f16'), ('Qbid', '<f16')])

    def update(self, orderbook, max_obs_size):
        """Return updated OrderBookSeries.
        It adds most recent state and remove oldest from OrderBookSeries if
        size conditions are exceeded keeping memory constraints in check
        """
        # Reverse bids because we want to both tops be the logical next in a deck
        depth = 25
        data = np.full(depth, np.nan, dtype=[('Pask', '<f16'), ('Qask', '<f16'), ('Pbid', '<f16'), ('Qbid', '<f16')])

        if len(orderbook.asks) + len(orderbook.bids):
            for idx, ask in enumerate(orderbook.asks):
                if idx == depth: break
                data['Pask'][idx] = np.longfloat(ask.price.amount)
                data['Qask'][idx] = np.longfloat(ask.amount.amount)

            for idx, bid in enumerate(orderbook.bids[::-1]):
                if idx == depth: break
                data['Pbid'][idx] = np.longfloat(bid.price.amount)
                data['Qbid'][idx] = np.longfloat(bid.amount.amount)

        # Build orderbook series data
        new_obs = OrderBookSeries(np.append(self.t, np.datetime64(time.time_ns(), "ns")),
                                  np.append(self.data, data).reshape(self.data.shape[0] + 1, depth))
        # Drop old data if at size limit
        if new_obs.t.shape[0] <= max_obs_size:
            return new_obs
        return OrderBookSeries(new_obs.t[1:], new_obs.data[1:])

    def bootstrap(api, config):
        """Initiate a OrderBookSeries with data from market."""
        # Populate the initial entries so that temporal derivatives are
        # meaningful and we can compute temporal statistics
        obs = OrderBookSeries()
        update_id = -1
        before = time.time()
        while (len(obs.t) < config["min_history_points"]) and (time.time() - before < config["max_loading_time"]):
            # Guard against network issues
            try:
                orderbook = api.get_order_book(config["market"])
            except:
                continue
            if orderbook.update_id != update_id:
                obs = obs.update(orderbook, config['max_obs_size'])
                update_id = orderbook.update_id
            # Avoid rate limit, give 100ms delay
            time.sleep(0.100)
        return obs