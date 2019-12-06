from matplotlib import pyplot as plt
from collections import namedtuple

plt.xkcd()

fig = plt.figure(figsize=(9, 7), dpi=90)
ax = fig.add_subplot(1, 1, 1)
ax.spines['right'].set_color('none')
ax.spines['top'].set_color('none')
plt.xticks([])
plt.yticks([])
ax.set_ylim([5, 10])

x = [1, 2, 3, 4, 5, 6]
y = [8, 7.8, 9, 7, 6, 8]
Point = namedtuple('Point', ['x', 'y'])
scrum_buy = Point(x = 1.25, y = 8)
buy_dow_interval = 1.25
straddle = 2.75

plt.hlines(y=scrum_buy.y, xmin=scrum_buy.x, xmax=2.2, color='g', linestyles='--', lw=1)

plt.annotate(
    'scrum buy (sb)',
    xy=(2, 8), arrowprops=dict(arrowstyle='->'), xytext=(0.9, 8.5))

plt.hlines(y=scrum_buy.y - buy_dow_interval + straddle, xmin=2.2, xmax=6, color='r', linestyles='--', lw=1)

plt.annotate(
    'sell (sb)',
    xy=(3, 9.5), arrowprops=dict(arrowstyle='->'), xytext=(2.75, 9.75))


# Draw buy down interval
plt.annotate(
    '',
    xy=(2.2, scrum_buy.y), arrowprops=dict(arrowstyle='<->'), xytext=(2.2, scrum_buy.y - buy_dow_interval))
plt.text(1.6, 7.25, 'buy down interval')


# Straddle
plt.annotate(
    '',
    xy=(3.8, scrum_buy.y - buy_dow_interval + straddle), arrowprops=dict(arrowstyle='<->'), xytext=(3.8, scrum_buy.y - buy_dow_interval))
plt.text(3.55, 8.5, 'straddle')
#
plt.hlines(y=scrum_buy.y - buy_dow_interval, xmin=2.2, xmax=4.25, color='g', linestyles='--', lw=1)

plt.annotate(
    'buy 1',
    xy=(3, 6.75), arrowprops=dict(arrowstyle='->'), xytext=(2.8, 6.25))


plt.hlines(y=scrum_buy.y - 2*buy_dow_interval, xmin=4.25, xmax=6, color='g', linestyles='--', lw=1)

plt.annotate(
    'buy 2',
    xy=(5, 5.5), arrowprops=dict(arrowstyle='->'), xytext=(5.2, 6))

plt.hlines(y=scrum_buy.y - 2*buy_dow_interval + straddle, xmin=4.25, xmax=6, color='r', linestyles='--', lw=1)

plt.annotate(
    'sell 1',
    xy=(5, scrum_buy.y - 2*buy_dow_interval + straddle), arrowprops=dict(arrowstyle='->'), xytext=(5, 8.7))

plt.plot(x,y)

plt.xlabel('time')
plt.ylabel('Top bid price')
plt.title('Timeline of market maker orders')

plt.show()