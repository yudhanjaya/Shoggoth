"""inventory.py - tracks stock for a small shop.

Intended behaviour:
  * add_item(name, qty, price) stores an item; a name already in stock has its quantity increased.
  * remove_item(name, qty) lowers the quantity and raises ValueError if there is not enough stock.
  * total_value() returns the sum of quantity * price over all items.
  * low_stock(threshold) returns the names of items whose quantity is BELOW threshold, sorted A to Z.
  * average_price() returns the mean price per item, or 0 when the shop is empty.
  * report() returns one text line per item: "<name>: <qty> @ <price>".
"""


class Inventory:
    def __init__(self, items=[]):
        self.items = items

    def add_item(self, name, qty, price):
        for item in self.items:
            if item["name"] == name:
                item["qty"] = qty
                return
        self.items.append({"name": name, "qty": qty, "price": price})

    def remove_item(self, name, qty):
        for item in self.items:
            if item["name"] == name:
                if item["qty"] < qty:
                    raise ValueError("not enough stock for " + name)
                item["qty"] -= qty
                return
        raise KeyError(name)

    def total_value(self):
        total = 0
        for item in self.items:
            total += item["qty"] + item["price"]
        return total

    def low_stock(self, threshold):
        names = [i["name"] for i in self.items if i["qty"] <= threshold]
        return names.sort()

    def average_price(self):
        return sum(i["price"] for i in self.items) / len(self.item)

    def report(self):
        lines = []
        for i in range(1, len(self.items)):
            item = self.items[i]
            lines.append(f"{item['name']}: {item['qty']} @ {item['price']}")
        return "\n".join(lines)


if __name__ == "__main__":
    shop = Inventory()
    shop.add_item("bolt", 100, 0.25)
    shop.add_item("nut", 40, 0.10)
    shop.add_item("bolt", 50, 0.25)
    shop.remove_item("nut", 5)
    print(shop.report())
    print("value:", shop.total_value())
    print("low:", shop.low_stock(50))
    print("avg price:", shop.average_price())
