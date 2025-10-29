"""
Setup script to initialize the SQLite sales database with sample data.
Run this once before running the main example.
"""

import sqlite3
import random
from datetime import datetime, timedelta
from pathlib import Path


def create_database(db_path: str):
	"""Create and populate sales database."""

	print(f"📁 Creating sales database: {db_path}")

	conn = sqlite3.connect(db_path)
	cursor = conn.cursor()

	# Create sales table
	cursor.execute("""
        CREATE TABLE IF NOT EXISTS sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name TEXT NOT NULL,
            category TEXT NOT NULL,
            region TEXT NOT NULL,
            sales_rep TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            unit_price REAL NOT NULL,
            total_amount REAL NOT NULL,
            sale_date TEXT NOT NULL,
            customer_segment TEXT NOT NULL
        )
    """)

	# Create products table
	cursor.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name TEXT UNIQUE NOT NULL,
            category TEXT NOT NULL,
            cost REAL NOT NULL,
            margin_percentage REAL NOT NULL
        )
    """)

	# Create sales_reps table
	cursor.execute("""
        CREATE TABLE IF NOT EXISTS sales_reps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            region TEXT NOT NULL,
            hire_date TEXT NOT NULL,
            performance_tier TEXT NOT NULL
        )
    """)

	print("✅ Tables created")

	# Insert products
	products = [
		("Laptop Pro", "Electronics", 800.00, 30.00),
		("Wireless Mouse", "Electronics", 15.00, 50.00),
		("Office Chair", "Furniture", 150.00, 40.00),
		("Standing Desk", "Furniture", 300.00, 35.00),
		("Monitor 27in", "Electronics", 200.00, 35.00),
		("Keyboard Mechanical", "Electronics", 80.00, 45.00),
		("Desk Lamp", "Furniture", 40.00, 50.00),
		("Webcam HD", "Electronics", 60.00, 40.00),
	]

	cursor.execute("DELETE FROM products")
	cursor.executemany(
		"""
        INSERT INTO products (product_name, category, cost, margin_percentage)
        VALUES (?, ?, ?, ?)
    """,
		products,
	)

	# Insert sales reps
	sales_reps = [
		("Alice Johnson", "North", "2020-01-15", "Top Performer"),
		("Bob Smith", "South", "2019-06-20", "High Performer"),
		("Carol White", "East", "2021-03-10", "Top Performer"),
		("David Brown", "West", "2020-11-05", "Average"),
		("Eve Davis", "North", "2022-02-01", "High Performer"),
		("Frank Miller", "South", "2021-08-15", "Average"),
	]

	cursor.execute("DELETE FROM sales_reps")
	cursor.executemany(
		"""
        INSERT INTO sales_reps (name, region, hire_date, performance_tier)
        VALUES (?, ?, ?, ?)
    """,
		sales_reps,
	)

	# Generate 500 sales records
	cursor.execute("DELETE FROM sales")
	sales_data = []

	regions = ["North", "South", "East", "West"]
	segments = ["Enterprise", "SMB", "Consumer"]

	start_date = datetime.now() - timedelta(days=365)

	for _ in range(500):
		product = random.choice(products)
		product_name, category, cost, margin = product

		quantity = random.randint(1, 20)
		unit_price = cost * (1 + margin / 100)
		total = quantity * unit_price

		sale_date = start_date + timedelta(days=random.randint(0, 365))

		sales_data.append(
			(
				product_name,
				category,
				random.choice(regions),
				random.choice([rep[0] for rep in sales_reps]),
				quantity,
				round(unit_price, 2),
				round(total, 2),
				sale_date.date().isoformat(),
				random.choice(segments),
			)
		)

	cursor.executemany(
		"""
        INSERT INTO sales (product_name, category, region, sales_rep, quantity,
                          unit_price, total_amount, sale_date, customer_segment)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
		sales_data,
	)

	conn.commit()

	# Verify
	cursor.execute("SELECT COUNT(*) FROM sales")
	count = cursor.fetchone()[0]

	print(f"✅ Inserted {count} sales records")

	cursor.close()
	conn.close()

	print("\n📊 Database ready!")
	print(f"   Location: {db_path}")
	print("   Tables: sales, products, sales_reps")
	print("\n✅ Setup complete! You can now run main.py")


if __name__ == "__main__":
	db_path = Path(__file__).parent / "sales.db"
	create_database(str(db_path))
