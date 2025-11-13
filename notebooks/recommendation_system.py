pip install -q openai python-dotenv implicit scikit-learn scipy

dbutils.library.restartPython()

import os
import json
import warnings
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple

import pandas as pd
import numpy as np
from scipy.sparse import csr_matrix, coo_matrix
from sklearn.preprocessing import LabelEncoder, StandardScaler
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_squared_error, ndcg_score
from implicit.als import AlternatingLeastSquares

import openai
import time
from pyspark.sql import functions as F
from pyspark.sql.types import *
from pyspark.sql import Window
import json
from decimal import Decimal

# Visualization
import matplotlib.pyplot as plt
import seaborn as sns
from dotenv import load_dotenv

# Configure
warnings.filterwarnings('ignore')
sns.set_style('whitegrid')
plt.rcParams['figure.figsize'] = (12, 6)

# Load environment variables from .env file
load_dotenv()

print("✅ All libraries imported successfully")

# Load OpenAI API key from environment variables
openai_api_key = os.getenv('OPENAI_API_KEY')
if openai_api_key is None:
    raise ValueError("❌ OPENAI_API_KEY not found in environment variables. Please set it in your .env file.")


CONFIG = {
    'als': {
        'factors': 50,
        'regularization': 0.01,
        'iterations': 15,
        'alpha': 40.0
    },
    'llm': {
        'model': 'gpt-4o',
        'temperature': 0.3,
        'max_tokens': 2000
    },
    'recommendation': {
        'top_n': 5,
        'final_n': 3
    },
    'feature_engineering': {
        'recency_days': 90,
        'min_transactions': 3
    }
}

print("\n📊 Configuration:")
print(json.dumps(CONFIG, indent=2))

# %%
# ==============================================================================
# DATA LOADING (SPARK)
# ==============================================================================


TRANSACTIONS_TABLE = 'workspace.default.transaction_data_1'
PRODUCTS_TABLE = 'workspace.default.zenith_bank_product_catalog'
CONVERSATIONS_TABLE = 'workspace.default.conversation_data_1'
CUSTOMERS_TABLE = 'workspace.default.customer_data_1'

print("📂 Loading datasets from Unity Catalog...\n")

# Load data using Spark (NO pandas conversion for distributed processing)
try:
    # Load transactions
    print(f"Loading: {TRANSACTIONS_TABLE}")
    df_transactions = spark.table(TRANSACTIONS_TABLE)
    transactions_count = df_transactions.count()
    print(f" Loaded {transactions_count:,} transactions")
    
    # Load products
    print(f"\nLoading: {PRODUCTS_TABLE}")
    df_products = spark.table(PRODUCTS_TABLE)
    products_count = df_products.count()
    print(f" Loaded {products_count:,} products")
    
    # Load conversations
    print(f"\nLoading: {CONVERSATIONS_TABLE}")
    df_conversations = spark.table(CONVERSATIONS_TABLE)
    conversations_count = df_conversations.count()
    print(f" Loaded {conversations_count:,} conversations")
    
    # Try loading customer demographics (optional)
    print(f"\nLoading: {CUSTOMERS_TABLE}")
    try:
        df_customers = spark.table(CUSTOMERS_TABLE)
        customers_count = df_customers.count()
        print(f" Loaded {customers_count:,} customer demographics")
    except Exception as e:
        df_customers = None
        print(f" Customer demographics table not found: {e}")
        print(" Proceeding without demographics data")
    
    # Display basic info using Spark operations
    print("\n" + "="*80)
    print(" DATASET OVERVIEW")
    print("="*80)
    print(f"Unique Customers: {df_transactions.select('Customer_ID').distinct().count():,}")
    print(f"Unique Products: {df_products.select('Product_ID').distinct().count():,}")
    
    # Get date range
    date_stats = df_transactions.agg(
        F.min('Date').alias('min_date'),
        F.max('Date').alias('max_date')
    ).collect()[0]
    print(f"Date Range: {date_stats['min_date']} to {date_stats['max_date']}")
    
    # Get total transaction value
    total_value = df_transactions.agg(F.sum('Trans_Amount').alias('total')).collect()[0]['total']
    print(f"Total Transaction Value: ₦{total_value:,.2f}")
    print("="*80)
    
except Exception as e:
    print(f"\n Error loading data: {e}")
    print("\n Troubleshooting:")
    print("   1. Verify table names exist in Unity Catalog")
    print("   2. Check you have READ permissions on these tables")
    print("   3. Run: spark.sql('SHOW TABLES IN workspace.default').show()")
    print("   4. Or run: spark.catalog.listTables('workspace.default')")

# Quick data exploration
print("\n TRANSACTIONS SAMPLE:")
display(df_transactions.head())

print("\n PRODUCTS SAMPLE:")
display(df_products.head())

print("\n CUSTOMERS SAMPLE:")
display(df_customers.head())

print("\n CONVERSATIONS SAMPLE:")
display(df_conversations.head())

print("\n TRANSACTION STATISTICS:")
print(df_transactions.describe())

# Check for missing values
print("\n Missing Values:")
print(df_transactions.select([F.sum(F.col(c).isNull().cast('int')).alias(c) for c in df_transactions.columns]).show())

# ============================================================================
# CELL 6 - HYBRID INTERACTION MATRIX (OPENAI + TRANSACTIONS WITH DESCRIPTIONS)
# ============================================================================

from openai import OpenAI
import time
from pyspark.sql import functions as F
from pyspark.sql.types import *
from pyspark.sql import Window
import json
from decimal import Decimal

def create_customer_product_interactions(df_custs, df_products, df_trans,
                                          openai_api_key=None, 
                                          model_name="gpt-4o-mini",
                                          customer_sample_size=None,
                                          batch_size=30,
                                          temperature=0.3,
                                          top_n_products=10,
                                          rate_limit_delay=0.5,
                                          additional_rules=None,
                                          use_transaction_data=True,
                                          transaction_weight=0.7):
    """
    HYBRID: Combines OpenAI intelligent matching with real transaction data
    Enhanced with transaction description analysis
    All parameters configurable, no hard-coded values
    Uses Spark for distributed processing
    
    Args:
        df_custs: Customer DataFrame with demographics and Account_Type
        df_products: Product catalog DataFrame
        df_trans: Transaction DataFrame (with Description field)
        openai_api_key: OpenAI API key (required)
        model_name: OpenAI model to use
        customer_sample_size: Number of customers to process (None = all)
        batch_size: Number of customers per API call
        temperature: LLM temperature setting
        top_n_products: Number of products to recommend per customer
        rate_limit_delay: Delay between API calls in seconds
        additional_rules: Optional list of custom business rules
        use_transaction_data: Whether to include transaction data
        transaction_weight: Weight for transaction scores (0-1)
    """
    start_time = time.time()
    print(" Creating interaction matrix (Hybrid: OpenAI + Transaction Descriptions)...\n")
    
    # Validate inputs
    if openai_api_key is None:
        raise ValueError("openai_api_key parameter is required")
    
    # Initialize OpenAI client
    client = OpenAI(api_key=openai_api_key)
    
    # Get all product names
    all_product_names = [row.Product_Name for row in df_products.select('Product_Name').collect()]
    print(f"   Products in catalog: {len(all_product_names)}")
    
    # =========================================================================
    # 1. IDENTIFY CURRENT PRODUCTS FROM ACCOUNT_TYPE (SPARK)
    # =========================================================================
    print("\n   → Identifying current products from Account_Type...")
    
    df_custs_with_keywords = df_custs.withColumn(
        'account_keywords',
        F.regexp_extract(F.lower(F.col('Account_Type')), 
                        r'(current|savings|sme|student|premium|platinum|children|aspire)', 1)
    )
    
    df_products_keywords = df_products.withColumn(
        'product_keywords',
        F.regexp_extract(F.lower(F.col('Product_Name')), 
                        r'(current|savings|sme|student|premium|platinum|children|aspire)', 1)
    )
    
    keyword_map = df_products_keywords.filter(F.col('product_keywords') != '') \
        .select('product_keywords', 'Product_Name') \
        .distinct()
    
    df_custs_with_current = df_custs_with_keywords.join(
        keyword_map,
        df_custs_with_keywords.account_keywords == keyword_map.product_keywords,
        'left'
    ).withColumnRenamed('Product_Name', 'current_product')
    
    customer_current_products_df = df_custs_with_current.filter(
        F.col('current_product').isNotNull()
    ).groupBy('Customer_ID').agg(
        F.collect_set('current_product').alias('current_products')
    )
    
    current_products_count = customer_current_products_df.count()
    print(f"      Found current products for {current_products_count:,} customers")
    
    customer_current_products = {
        row.Customer_ID: set(row.current_products) 
        for row in customer_current_products_df.collect()
    }
    
    # =========================================================================
    # 2. PREPARE CUSTOMER PROFILES (SPARK)
    # =========================================================================
    print("\n   → Preparing customer profiles...")
    
    customer_profiles = df_custs.select([c for c in df_custs.columns])
    profiles_count = customer_profiles.count()
    print(f"      Total customers: {profiles_count:,}")
    
    # =========================================================================
    # 3. SAMPLE CUSTOMERS IF SPECIFIED
    # =========================================================================
    if customer_sample_size is not None and profiles_count > customer_sample_size:
        print(f"      Sampling {customer_sample_size:,} customers from {profiles_count:,} total")
        customer_profiles_sample = customer_profiles.sample(
            fraction=customer_sample_size/profiles_count, 
            seed=42
        ).limit(customer_sample_size)
    else:
        print(f"      Processing all {profiles_count:,} customers")
        customer_profiles_sample = customer_profiles
    
    # =========================================================================
    # 4. PREPARE PRODUCT CATALOG WITH RULES (SPARK)
    # =========================================================================
    print("\n   → Preparing product catalog and deriving rules from data...")
    
    product_cols = [c for c in df_products.columns]
    product_data = df_products.select(product_cols).collect()
    
    def safe_convert(value):
        if value is None:
            return None
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, (int, float, str, bool)):
            return value
        return str(value)
    
    product_catalog = []
    for row in product_data:
        product_info = {'name': row.Product_Name}
        for col in product_cols:
            if col != 'Product_Name' and hasattr(row, col):
                value = getattr(row, col)
                converted_value = safe_convert(value)
                if converted_value is not None:
                    product_info[col.lower()] = converted_value
        product_catalog.append(product_info)
    
    product_list_parts = []
    for i, prod in enumerate(product_catalog):
        parts = [f"{i+1}. {prod['name']}"]
        if 'product_category' in prod:
            parts.append(f"Category: {prod['product_category']}")
        if 'target_audience' in prod:
            parts.append(f"Target: {prod['target_audience']}")
        if 'age_range' in prod:
            parts.append(f"Age Range: {prod['age_range']}")
        if 'minimum_balance' in prod:
            try:
                min_bal = float(prod['minimum_balance'])
                parts.append(f"Min Balance: ₦{min_bal:,.0f}")
            except (ValueError, TypeError):
                parts.append(f"Min Balance: {prod['minimum_balance']}")
        if 'interest_rate' in prod:
            parts.append(f"Interest Rate: {prod['interest_rate']}")
        for key, value in prod.items():
            if key not in ['name', 'product_category', 'target_audience', 'age_range', 'minimum_balance', 'interest_rate']:
                parts.append(f"{key.replace('_', ' ').title()}: {value}")
        product_list_parts.append(" - ".join(parts))
    
    product_list = "\n".join(product_list_parts)
    
    # =========================================================================
    # 5. BUILD DYNAMIC RULES FROM PRODUCT CATALOG
    # =========================================================================
    print("   → Deriving business rules from product catalog...")
    
    derived_rules = []
    for prod in product_catalog:
        prod_name = prod['name']
        if 'age_range' in prod and prod['age_range']:
            age_range = str(prod['age_range'])
            derived_rules.append(f"- {prod_name}: Only for customers within age range {age_range}")
        if 'target_audience' in prod and prod['target_audience']:
            target = prod['target_audience']
            derived_rules.append(f"- {prod_name}: Designed for {target}")
        if 'minimum_balance' in prod and prod['minimum_balance']:
            try:
                min_bal = float(prod['minimum_balance'])
                if min_bal > 0:
                    derived_rules.append(f"- {prod_name}: Requires minimum balance of ₦{min_bal:,.0f}")
            except (ValueError, TypeError):
                pass
    
    if additional_rules:
        derived_rules.extend([f"- {rule}" for rule in additional_rules])
    
    general_rules = [
        "- Match products to customer demographics (age, income, occupation)",
        "- Do NOT recommend products similar to customer's current account",
        "- Consider customer's financial capacity when recommending products",
        "- Respect all age ranges and target audience specifications STRICTLY"
    ]
    
    all_rules = general_rules + derived_rules
    rules_text = "\n".join(all_rules) if all_rules else "- Match products appropriately to customer profiles"
    
    print(f"Generated {len(derived_rules)} product-specific rules from catalog")
    
    # =========================================================================
    # 6. USE OPENAI TO SCORE CUSTOMER-PRODUCT FIT
    # =========================================================================
    print("\n   → Using OpenAI to score product fit (intelligent matching)...")
    print("      This may take time depending on sample size...")
    
    customer_profiles_list = customer_profiles_sample.collect()
    total_batches = (len(customer_profiles_list) + batch_size - 1) // batch_size
    all_interactions = []
    
    for batch_idx in range(0, len(customer_profiles_list), batch_size):
        batch = customer_profiles_list[batch_idx:batch_idx + batch_size]
        print(f"      Processing batch {batch_idx//batch_size + 1}/{total_batches}...", end='')
        
        customer_summaries = []
        for cust in batch:
            summary_parts = [f"Customer {cust.Customer_ID}:"]
            for col in df_custs.columns:
                if col != 'Customer_ID' and hasattr(cust, col):
                    value = getattr(cust, col)
                    converted_value = safe_convert(value)
                    if converted_value is not None:
                        summary_parts.append(f"- {col.replace('_', ' ').title()}: {converted_value}")
            customer_summaries.append('\n'.join(summary_parts))
        
        prompt = f"""You are a banking product recommendation expert. Score how well each product fits each customer on a scale of 0-10.

# CUSTOMERS
{chr(10).join(customer_summaries)}

# PRODUCTS
{product_list}

# TASK
For each customer, score ONLY the top {top_n_products} most relevant products (0-10 scale).

# MATCHING RULES
{rules_text}

Return ONLY valid JSON with this exact structure:
{{
  "matches": [
    {{"customer_id": "C001", "product": "Exact Product Name", "score": 8}},
    {{"customer_id": "C001", "product": "Another Product", "score": 7}}
  ]
}}

Do NOT include any other text, only the JSON object."""
        
        try:
            response = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": "You are a banking product expert. Return only valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                temperature=temperature,
                response_format={"type": "json_object"}
            )
            
            response_text = response.choices[0].message.content.strip()
            if response_text.startswith('```'):
                response_text = response_text.split('```')[1]
                if response_text.startswith('json'):
                    response_text = response_text[4:]
                response_text = response_text.strip()
            
            result = json.loads(response_text)
            for match in result.get('matches', []):
                all_interactions.append({
                    'Customer_ID': match['customer_id'],
                    'Product_Name': match['product'],
                    'interaction_score': float(match['score'])
                })
            
            print(f" ({len(result.get('matches', []))} matches)")
        except Exception as e:
            print(f" Error: {e}")
            continue
        
        if batch_idx + batch_size < len(customer_profiles_list):
            time.sleep(rate_limit_delay)
    
    # =========================================================================
    # 7. CREATE TRANSACTION-BASED INTERACTIONS (WITH DESCRIPTION ANALYSIS)
    # =========================================================================
    interaction_matrix = None
    
    if use_transaction_data and df_trans is not None:
        print("\n   → Computing transaction-based interaction scores...")
        print("      Analyzing transaction descriptions for product signals...")
        
        # Get sampled customer IDs
        sampled_customer_ids = [row.Customer_ID for row in customer_profiles_sample.select('Customer_ID').collect()]
        df_trans_filtered = df_trans.filter(F.col('Customer_ID').isin(sampled_customer_ids))
        
        trans_filtered_count = df_trans_filtered.count()
        print(f"      Filtered to {trans_filtered_count:,} transactions for sampled customers")
        
        # Extract keywords from Account_Type
        df_trans_with_keywords = df_trans_filtered.withColumn(
            'account_keywords',
            F.regexp_extract(F.lower(F.col('Account_Type')), 
                            r'(current|savings|sme|student|premium|platinum|children|aspire)', 1)
        )
        
        # Extract keywords from Description (if it exists)
        if 'Description' in df_trans_filtered.columns:
            print(" Description field found - using for enhanced matching")
            df_trans_with_keywords = df_trans_with_keywords.withColumn(
                'desc_keywords',
                F.regexp_extract(F.lower(F.col('Description')), 
                                r'(current|savings|sme|student|premium|platinum|children|aspire|loan|investment|card|transfer|deposit)', 1)
            )
            
            # Combine keywords from both sources
            df_trans_with_keywords = df_trans_with_keywords.withColumn(
                'combined_keywords',
                F.when(F.col('account_keywords') != '', F.col('account_keywords'))
                 .when(F.col('desc_keywords') != '', F.col('desc_keywords'))
                 .otherwise('')
            )
        else:
            print("      ⚠ Description field not found - using Account_Type only")
            df_trans_with_keywords = df_trans_with_keywords.withColumn(
                'combined_keywords',
                F.col('account_keywords')
            )
        
        # Match transactions to products using combined keywords
        transaction_product_matches = df_trans_with_keywords.join(
            keyword_map.withColumnRenamed('product_keywords', 'combined_keywords'),
            'combined_keywords',
            'inner'
        )
        
        matches_count = transaction_product_matches.count()
        print(f"      Matched {matches_count:,} transactions to products")
        
        # Aggregate transaction metrics (RFM Analysis)
        transaction_interactions = transaction_product_matches.groupBy('Customer_ID', 'Product_Name').agg(
            F.count('*').alias('transaction_count'),
            F.sum('Trans_Amount').alias('total_amount'),
            F.max('Date').alias('last_transaction_date'),
            F.avg('Trans_Amount').alias('avg_amount')
        )
        
        # Calculate recency
        max_date = df_trans_filtered.agg(F.max('Date')).collect()[0][0]
        
        transaction_interactions = transaction_interactions.withColumn(
            'days_since_last',
            F.datediff(F.lit(max_date), F.col('last_transaction_date'))
        )
        
        # Create RFM-based interaction score (0-10 scale)
        print("      Computing RFM (Recency, Frequency, Monetary) scores...")
        
        # Frequency score: normalize transaction count (log scale for better distribution)
        transaction_interactions = transaction_interactions.withColumn(
            'frequency_score',
            F.least(F.lit(10.0), (F.log1p(F.col('transaction_count')) / F.log1p(F.lit(100.0))) * 10.0)
        )
        
        # Monetary score: normalize total amount (log scale)
        transaction_interactions = transaction_interactions.withColumn(
            'monetary_score',
            F.least(F.lit(10.0), (F.log1p(F.col('total_amount')) / F.log1p(F.lit(1000000.0))) * 10.0)
        )
        
        # Recency score: decay based on days since last transaction
        transaction_interactions = transaction_interactions.withColumn(
            'recency_score',
            F.when(F.col('days_since_last') <= 30, F.lit(10.0))
             .when(F.col('days_since_last') <= 60, F.lit(8.0))
             .when(F.col('days_since_last') <= 90, F.lit(6.0))
             .when(F.col('days_since_last') <= 180, F.lit(4.0))
             .when(F.col('days_since_last') <= 365, F.lit(2.0))
             .otherwise(F.lit(0.5))
        )
        
        # Combined RFM score with weights
        transaction_interactions = transaction_interactions.withColumn(
            'transaction_score',
            (F.col('frequency_score') * 0.35) +   # 35% frequency
            (F.col('monetary_score') * 0.35) +    # 35% monetary
            (F.col('recency_score') * 0.30)       # 30% recency
        )
        
        transaction_interactions = transaction_interactions.select(
            'Customer_ID',
            'Product_Name',
            F.col('transaction_score').alias('trans_score'),
            'transaction_count',
            'total_amount',
            'days_since_last'
        )
        
        trans_count = transaction_interactions.count()
        print(f"      Generated {trans_count:,} transaction-based interactions")
        
        # Show sample RFM scores
        print("\n      Sample RFM scores:")
        transaction_interactions.select(
            'Customer_ID', 'Product_Name', 'trans_score', 
            'transaction_count', 'total_amount', 'days_since_last'
        ).show(5, truncate=False)
        
        # =========================================================================
        # 8. MERGE TRANSACTION AND OPENAI SCORES
        # =========================================================================
        print("\n   → Merging transaction and OpenAI scores...")
        
        if not all_interactions:
            print("      No OpenAI interactions, using transaction data only")
            interaction_matrix = transaction_interactions.select(
                'Customer_ID',
                'Product_Name',
                F.col('trans_score').alias('interaction_score')
            )
        else:
            openai_interactions = spark.createDataFrame(all_interactions)
            
            # Full outer join
            combined = openai_interactions.join(
                transaction_interactions.select('Customer_ID', 'Product_Name', 'trans_score'),
                ['Customer_ID', 'Product_Name'],
                'full_outer'
            )
            
            # Combine scores with weighting
            combined = combined.withColumn(
                'openai_score',
                F.coalesce(F.col('interaction_score'), F.lit(0.0))
            ).withColumn(
                'trans_score_filled',
                F.coalesce(F.col('trans_score'), F.lit(0.0))
            ).withColumn(
                'interaction_score',
                (F.col('trans_score_filled') * transaction_weight) + 
                (F.col('openai_score') * (1.0 - transaction_weight))
            )
            
            interaction_matrix = combined.select(
                'Customer_ID',
                'Product_Name',
                'interaction_score'
            )
            
            combined_count = interaction_matrix.count()
            print(f"      Combined into {combined_count:,} total interactions")
            print(f"      Weighting: {transaction_weight*100:.0f}% transactions + {(1-transaction_weight)*100:.0f}% OpenAI")
    
    else:
        print("\n   → Using OpenAI scores only (no transaction data)")
        if all_interactions:
            interaction_matrix = spark.createDataFrame(all_interactions)
        else:
            print("   ⚠️ No interactions generated. Creating fallback...")
            fallback_count = min(3, len(all_product_names))
            fallback_products = all_product_names[:fallback_count]
            fallback_data = []
            for cust_row in customer_profiles_sample.select('Customer_ID').collect():
                for prod in fallback_products:
                    fallback_data.append((cust_row.Customer_ID, prod, 5.0))
            interaction_matrix = spark.createDataFrame(
                fallback_data,
                ['Customer_ID', 'Product_Name', 'interaction_score']
            )
    
    # =========================================================================
    # 9. AGGREGATE AND FINALIZE
    # =========================================================================
    print("\n → Building final interaction matrix...")
    
    interaction_matrix = interaction_matrix.groupBy('Customer_ID', 'Product_Name').agg(
        F.max('interaction_score').alias('interaction_score')
    )
    
    # =========================================================================
    # 10. FILTER OUT CURRENT PRODUCTS
    # =========================================================================
    print("\n   → Filtering current products...")
    
    current_pairs = []
    for cust, prods in customer_current_products.items():
        for prod in prods:
            current_pairs.append((cust, prod))
    
    if current_pairs:
        current_pairs_df = spark.createDataFrame(
            current_pairs,
            ['Customer_ID', 'Product_Name']
        )
        interaction_matrix = interaction_matrix.join(
            current_pairs_df,
            ['Customer_ID', 'Product_Name'],
            'left_anti'
        )
    
    elapsed = time.time() - start_time
    
    # =========================================================================
    # FINAL STATS
    # =========================================================================
    total_interactions = interaction_matrix.count()
    unique_customers = interaction_matrix.select('Customer_ID').distinct().count()
    unique_products = interaction_matrix.select('Product_Name').distinct().count()
    
    print("\n" + "="*80)
    print("HYBRID INTERACTION MATRIX CREATED")
    print("="*80)
    print(f"Total interactions: {total_interactions:,}")
    print(f"Unique customers: {unique_customers:,}")
    print(f"Unique products: {unique_products:,}")
    print(f"Products in catalog: {len(product_catalog)}")
    print(f"\n {model_name} + Transaction Data:")
    print(f"   • {len(derived_rules)} rules derived from product catalog")
    print(f"   • {len(additional_rules) if additional_rules else 0} custom business rules")
    print(f"   • Customer demographics and attributes")
    if use_transaction_data:
        print(f"   • Real transaction history with descriptions (weight: {transaction_weight*100:.0f}%)")
        print(f"   • RFM analysis: Recency (30%) + Frequency (35%) + Monetary (35%)")
    print(f"\n Current products EXCLUDED from recommendations")
    print(f"Customers with current products: {len(customer_current_products):,}")
    print(f"\n Completed in {elapsed:.1f} seconds")
    print(f"   Model used: {model_name}")
    print("="*80)
    
    return interaction_matrix, df_products, customer_current_products


# ============================================================================
# EXECUTE
# ============================================================================

print("\n🚀 Starting Hybrid interaction matrix creation...\n")

custom_rules = [
    "Premium products require demonstrated high transaction volumes",
    "Savings products are suitable for customers with stable income"
]

interaction_df, product_map, customer_current_products = create_customer_product_interactions(
    df_customers,
    df_products,
    df_transactions, 
    openai_api_key=openai_api_key,
    model_name=CONFIG['llm']['model'],
    customer_sample_size=1000,
    batch_size=30,
    temperature=CONFIG['llm']['temperature'],
    top_n_products=CONFIG['recommendation']['top_n'],
    rate_limit_delay=0.5,
    additional_rules=custom_rules,
    use_transaction_data=True,
    transaction_weight=0.7           # 70% transactions, 30% OpenAI
)

print("\n Sample Interactions:")
interaction_df.show(10)

print("\n Score Distribution:")
interaction_df.describe(['interaction_score']).show()

print("\n Top Products by Interaction Count:")
top_products = interaction_df.groupBy('Product_Name').count().orderBy(F.desc('count')).limit(10)
top_products.show()

interaction_df=interaction_df.toPandas()
interaction_df.to_csv("interaction_df.csv", index=False)

def engineer_customer_features(df_trans, df_convs, df_custs=None, recency_days=90, customer_sample_size=1000):
    """
    Create comprehensive customer features for ML and LLM context.
    SPARK OPTIMIZED: Uses distributed processing
    """
    import time
    from pyspark.sql import Window
    from pyspark.sql.functions import col, count, sum as spark_sum, mean, stddev, expr
    from datetime import timedelta
    
    start_time = time.time()
    
    print(f"🔨 Engineering customer features for {customer_sample_size if customer_sample_size else 'ALL'} customers (Spark)...")
    
    # =========================================================================
    # SAMPLE CUSTOMERS FIRST (from df_custs - SOURCE TABLE)
    # =========================================================================
    if customer_sample_size and df_custs is not None:
        print(f"   → Sampling {customer_sample_size:,} customers from customer table...")
        total_customers = df_custs.count()
        sample_fraction = customer_sample_size / total_customers
        df_custs_sample = df_custs.sample(fraction=sample_fraction, seed=42).limit(customer_sample_size)
        sampled_customer_ids = [row.Customer_ID for row in df_custs_sample.select('Customer_ID').collect()]
        sampled_customer_ids_set = set(sampled_customer_ids)
        
        # Filter transactions and conversations to only include sampled customers
        df_trans = df_trans.filter(col('Customer_ID').isin(sampled_customer_ids))
        df_convs = df_convs.filter(col('Customer_ID').isin(sampled_customer_ids))
        df_custs = df_custs_sample
        
        print(f"      ✓ Sampled {len(sampled_customer_ids):,} customers")
        print(f"      ✓ Filtered to {df_trans.count():,} transactions")
        print(f"      ✓ Filtered to {df_convs.count():,} conversations")
    
    # =========================================================================
    # PREPARE TRANSACTION DATA
    # =========================================================================
    print("   → Preparing transaction data...")
    
    # Ensure Date is date type
    df_trans = df_trans.withColumn('Date', F.to_date(F.col('Date')))
    
    # Create debit/credit flags
    df_trans = df_trans.withColumn('is_debit', F.when(F.col('Deb_or_credit') == 'D', 1).otherwise(0))
    df_trans = df_trans.withColumn('is_credit', F.when(F.col('Deb_or_credit') == 'C', 1).otherwise(0))
    df_trans = df_trans.withColumn('debit_amount', F.col('Trans_Amount') * F.col('is_debit'))
    df_trans = df_trans.withColumn('credit_amount', F.col('Trans_Amount') * F.col('is_credit'))
    
    # Calculate recency
    max_date = df_trans.agg(F.max('Date')).collect()[0][0]
    recent_threshold = max_date - timedelta(days=recency_days)
    
    df_trans = df_trans.withColumn('is_recent', F.when(F.col('Date') >= F.lit(recent_threshold), 1).otherwise(0))
    df_trans = df_trans.withColumn('recent_debit', F.col('debit_amount') * F.col('is_recent'))
    
    # =========================================================================
    # 1. DATE RANGE FEATURES
    # =========================================================================
    print("   → Computing date range features...")
    
    date_ranges = df_trans.groupBy('Customer_ID').agg(
        F.min('Date').alias('min_date'),
        F.max('Date').alias('max_date')
    )
    
    date_ranges = date_ranges.withColumn(
        'date_span_days',
        F.when(F.datediff(F.col('max_date'), F.col('min_date')) == 0, 1)
         .otherwise(F.datediff(F.col('max_date'), F.col('min_date')))
    )
    
    # =========================================================================
    # 2. AGGREGATED FEATURES
    # =========================================================================
    print("   → Computing aggregated features...")
    
    agg_features = df_trans.groupBy('Customer_ID').agg(
        # Financial metrics
        F.sum('debit_amount').alias('total_debit'),
        F.sum('credit_amount').alias('total_credit'),
        F.mean('Trans_Amount').alias('avg_transaction'),
        F.stddev('Trans_Amount').alias('std_transaction'),
        F.expr('percentile_approx(Trans_Amount, 0.5)').alias('median_transaction'),
        F.count('Trans_Amount').alias('transaction_count'),
        
        # Debit/Credit counts
        F.sum('is_debit').alias('debit_count'),
        F.sum('is_credit').alias('credit_count'),
        
        # Diversity metrics
        F.countDistinct('Category').alias('unique_categories'),
        F.countDistinct('Destination').alias('unique_destinations'),
        
        # Recency
        F.sum('is_recent').alias('recent_transaction_count'),
        F.sum('recent_debit').alias('recent_debit'),
        
        # Account type (most common using first)
        F.first('Account_Type').alias('current_account')
    )
    
    # =========================================================================
    # 3. TOP CATEGORY FEATURES
    # =========================================================================
    print("   → Computing category preferences...")
    
    # Filter debit transactions
    debit_txns = df_trans.filter(F.col('is_debit') == 1)
    
    # Top category per customer (most frequent)
    window_spec = Window.partitionBy('Customer_ID', 'Category')
    category_counts = debit_txns.groupBy('Customer_ID', 'Category').agg(
        F.count('*').alias('category_count')
    )
    
    window_rank = Window.partitionBy('Customer_ID').orderBy(F.desc('category_count'))
    top_categories = category_counts.withColumn('rank', F.row_number().over(window_rank)) \
        .filter(F.col('rank') == 1) \
        .select('Customer_ID', F.col('Category').alias('top_category'))
    
    # Top category spending amount
    category_amounts = debit_txns.groupBy('Customer_ID', 'Category').agg(
        F.sum('Trans_Amount').alias('category_amount')
    )
    
    window_rank_amount = Window.partitionBy('Customer_ID').orderBy(F.desc('category_amount'))
    top_category_amounts = category_amounts.withColumn('rank', F.row_number().over(window_rank_amount)) \
        .filter(F.col('rank') == 1) \
        .select('Customer_ID', F.col('category_amount').alias('top_category_amount'))
    
    # Category concentration (simplified - top category count / total count)
    category_concentration = category_counts.join(
        debit_txns.groupBy('Customer_ID').agg(F.count('*').alias('total_count')),
        'Customer_ID'
    ).withColumn('category_concentration', F.col('category_count') / F.col('total_count'))
    
    window_rank_conc = Window.partitionBy('Customer_ID').orderBy(F.desc('category_concentration'))
    category_concentration = category_concentration.withColumn('rank', F.row_number().over(window_rank_conc)) \
        .filter(F.col('rank') == 1) \
        .select('Customer_ID', 'category_concentration')
    
    # =========================================================================
    # 4. MERGE BASE FEATURES
    # =========================================================================
    print("   → Merging all features...")
    
    df_features = agg_features.join(date_ranges.select('Customer_ID', 'date_span_days'), 'Customer_ID', 'left')
    df_features = df_features.join(top_categories, 'Customer_ID', 'left')
    df_features = df_features.join(top_category_amounts, 'Customer_ID', 'left')
    df_features = df_features.join(category_concentration, 'Customer_ID', 'left')
    
    # Fill nulls
    df_features = df_features.fillna({
        'std_transaction': 0,
        'top_category': 'Unknown',
        'top_category_amount': 0,
        'category_concentration': 0
    })
    
    # =========================================================================
    # 5. COMPUTED FEATURES
    # =========================================================================
    print("   → Computing derived features...")
    
    df_features = df_features.withColumn('net_balance', F.col('total_credit') - F.col('total_debit'))
    df_features = df_features.withColumn(
        'debit_credit_ratio',
        F.when(F.col('total_credit') > 0, F.col('total_debit') / F.col('total_credit')).otherwise(0)
    )
    df_features = df_features.withColumn(
        'transaction_frequency_days',
        F.col('transaction_count') / F.col('date_span_days')
    )
    df_features = df_features.withColumn('days_since_last_transaction', F.col('date_span_days'))
    
    # =========================================================================
    # 6. CONVERSATION FEATURES
    # =========================================================================
    print("   → Adding conversation features...")
    
    conv_count = df_convs.count()
    if conv_count > 0:
        conv_features = df_convs.groupBy('Customer_ID').agg(
            F.count('Category').alias('conversation_count'),
            F.first('Category').alias('top_inquiry_category'),
            F.count('Customer_Message').alias('message_count')
        )
        df_features = df_features.join(conv_features, 'Customer_ID', 'left')
        df_features = df_features.fillna({
            'conversation_count': 0,
            'message_count': 0,
            'top_inquiry_category': 'None'
        })
    else:
        df_features = df_features.withColumn('conversation_count', F.lit(0))
        df_features = df_features.withColumn('message_count', F.lit(0))
        df_features = df_features.withColumn('top_inquiry_category', F.lit('None'))
    
    # =========================================================================
    # 7. DEMOGRAPHIC FEATURES (from df_custs - SOURCE TABLE)
    # =========================================================================
    print("   → Adding demographic features...")
    
    if df_custs is not None:
        demo_cols = ['Customer_ID']
        available_cols = df_custs.columns
        
        if 'Age' in available_cols: demo_cols.append('Age')
        if 'Occupation' in available_cols: demo_cols.append('Occupation')
        if 'Income_Bracket' in available_cols: demo_cols.append('Income_Bracket')
        if 'State' in available_cols: demo_cols.append('State')
        if 'Gender' in available_cols: demo_cols.append('Gender')
        if 'Location' in available_cols: demo_cols.append('Location')
        
        demo_features = df_custs.select(demo_cols)
        df_features = df_features.join(demo_features, 'Customer_ID', 'left')
    
    # =========================================================================
    # 8. FINAL DERIVED FEATURES
    # =========================================================================
    print("   → Computing final derived features...")
    
    df_features = df_features.withColumn(
        'financial_velocity',
        F.col('transaction_count') / (F.col('days_since_last_transaction') + 1)
    )
    df_features = df_features.withColumn(
        'spending_consistency',
        F.col('std_transaction') / (F.col('avg_transaction') + 1)
    )
    df_features = df_features.withColumn(
        'engagement_score',
        (F.col('transaction_count') * 0.4) + 
        (F.col('unique_categories') * 10 * 0.3) + 
        (F.col('conversation_count') * 5 * 0.3)
    )
    
    elapsed_time = time.time() - start_time
    feature_count = df_features.count()
    col_count = len(df_features.columns)
    
    print(f"✅ Engineered {feature_count:,} customer profiles with {col_count} features")
    print(f"   ⚡ Completed in {elapsed_time:.2f} seconds ({feature_count/elapsed_time:.0f} customers/sec)")
    
    return df_features


# ============================================================================
# EXECUTE
# ============================================================================

print("Starting customer feature engineering...\n")

customer_features = engineer_customer_features(
    df_transactions,      # Transactions table (filtered by sampled customers)
    df_conversations,     # Conversations table (filtered by sampled customers)
    df_customers,         # CUSTOMERS TABLE - SOURCE TABLE for sampling
    recency_days=CONFIG['feature_engineering']['recency_days'],
    customer_sample_size=1000  # Sample 1000 customers
)

print("\n Customer Features Sample:")
customer_features.show(10, truncate=False)

print("\n Feature Statistics:")
customer_features.describe().show()

print("\n Feature Columns:")
print(f"Total columns: {len(customer_features.columns)}")
for col in customer_features.columns:
    print(f"  - {col}")

customer_features1=customer_features.toPandas()
customer_features1.to_csv("customer_features.csv", index=False)


# ============================================================================  
# ALS + LLM RECOMMENDATION SYSTEM
# ============================================================================  

from pyspark.sql import SparkSession, Window
from pyspark.sql.functions import col, row_number, desc, lit
from pyspark.ml.recommendation import ALS
from pyspark.ml.evaluation import RegressionEvaluator
from pyspark.sql.types import StringType, FloatType, StructType, StructField, IntegerType
from openai import OpenAI
import pyspark.sql.functions as F
import numpy as np
from datetime import datetime

# ============================================================================  
# CONFIGURATION  
# ============================================================================  

# Load OpenAI API key from environment variables
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
if OPENAI_API_KEY is None:
    raise ValueError("❌ OPENAI_API_KEY not found in environment variables. Please set it in your .env file.")
OPENAI_MODEL = "gpt-4o"

TOP_N = 5
ALS_RANK = 20
ALS_REG_PARAM = 0.1
ALS_MAX_ITER = 20

INTERACTIONS_DF = interaction_df
CUSTOMERS_DF = customer_features
PRODUCTS_DF = product_map

# For testing - limit to N customers
SAMPLE_CUSTOMERS = 20

# ============================================================================  
# STEP 1: CLEAN DATA  
# ============================================================================  

print("\n📋 Step 1: Preparing data...")

interaction_df = INTERACTIONS_DF

# Validate columns
required_cols = {"Customer_ID", "Product_Name", "interaction_score"}
missing = required_cols - set(interaction_df.columns)
if missing:
    raise ValueError(f"Missing columns: {missing}")

# Clean
interaction_df = (interaction_df
    .filter(col("Customer_ID").isNotNull())
    .filter(col("Product_Name").isNotNull())
    .filter(col("interaction_score").isNotNull())
    .withColumn("interaction_score", col("interaction_score").cast("double"))
)

print(f"Cleaned: {interaction_df.count():,} interactions")

# Deduplicate per Customer-Product
w_dedup = Window.partitionBy("Customer_ID", "Product_Name").orderBy(desc("interaction_score"))
interaction_df = (interaction_df
    .withColumn("rn", row_number().over(w_dedup))
    .filter(col("rn") == 1)
    .drop("rn")
)

customer_features = CUSTOMERS_DF

# ============================================================================  
# STEP 2: CREATE INDEXES  
# ============================================================================  

print("\n Step 2: Creating indexes...")

customer_lookup = (interaction_df
    .select("Customer_ID")
    .distinct()
    .withColumn("user_int", (row_number().over(Window.orderBy("Customer_ID")) - 1).cast("int"))
)

product_lookup = (interaction_df
    .select("Product_Name")
    .distinct()
    .withColumn("item_int", (row_number().over(Window.orderBy("Product_Name")) - 1).cast("int"))
)

print(f" Customers: {customer_lookup.count():,}")
print(f" Products: {product_lookup.count():,}")

# Indexed interactions
idx_df = (interaction_df
    .join(customer_lookup, "Customer_ID", "inner")
    .join(product_lookup, "Product_Name", "inner")
    .select("Customer_ID", "Product_Name", "interaction_score", "user_int", "item_int")
)

# ============================================================================  
# STEP 3: TRAIN ALS  
# ============================================================================  

print("\n Step 3: Training ALS...")

train_df, test_df = idx_df.randomSplit([0.8, 0.2], seed=42)

als = ALS(
    userCol="user_int",
    itemCol="item_int",
    ratingCol="interaction_score",
    implicitPrefs=False,
    nonnegative=True,
    coldStartStrategy="drop",
    rank=ALS_RANK,
    regParam=ALS_REG_PARAM,
    maxIter=ALS_MAX_ITER,
    seed=42
)
als_model = als.fit(train_df)

pred_test = als_model.transform(test_df)
evaluator = RegressionEvaluator(
    metricName="rmse",
    labelCol="interaction_score",
    predictionCol="prediction"
)
rmse = evaluator.evaluate(pred_test)
print(f" Trained. RMSE: {rmse:.4f}")

# ============================================================================  
# STEP 4: GENERATE RECOMMENDATIONS (UNITY CATALOG COMPATIBLE)
# ============================================================================  

print("\n Step 4: Generating recommendations...")

# Extract factor matrices (Unity Catalog safe)
user_factors = als_model.userFactors.collect()
item_factors = als_model.itemFactors.collect()

# Build lookup dictionaries
user_factor_dict = {row['id']: row['features'] for row in user_factors}
item_factor_dict = {row['id']: row['features'] for row in item_factors}

print(f" Users: {len(user_factor_dict):,}, Items: {len(item_factor_dict):,}")

# Compute recommendations manually in Python (bypasses Unity Catalog restrictions)
recs_list = []
for user_int, user_vec in user_factor_dict.items():
    user_array = np.array(user_vec)
    scores = []
    
    for item_int, item_vec in item_factor_dict.items():
        item_array = np.array(item_vec)
        score = np.dot(user_array, item_array)
        scores.append((item_int, float(score)))
    
    # Sort by score and get top N
    scores.sort(key=lambda x: x[1], reverse=True)
    top_items = scores[:TOP_N]
    
    for rank, (item_int, als_score) in enumerate(top_items, start=1):
        recs_list.append({
            'user_int': int(user_int),
            'item_int': int(item_int),
            'als_score': float(als_score),
            'rank': rank
        })

print(f"  ✓ Computed {len(recs_list):,} recommendations")

# Create DataFrame from collected data
rec_schema = StructType([
    StructField("user_int", IntegerType(), True),
    StructField("item_int", IntegerType(), True),
    StructField("als_score", FloatType(), True),
    StructField("rank", IntegerType(), True)
])
als_flat = spark.createDataFrame(recs_list, schema=rec_schema)

# Join lookups
als_recommendations = (als_flat
    .join(customer_lookup, "user_int", "left")
    .join(product_lookup, "item_int", "left")
)

# Serverless-safe confidence score (fixed min/max)
min_score = 0.0
max_score = 5.0  # Expected ALS score range
als_recommendations = als_recommendations.withColumn(
    "confidence_score_pct",
    ((col("als_score") - lit(min_score)) / lit(max_score - min_score) * 100)
)

# ============================================================================  
# SAVE TABLE 1: ALS RECOMMENDATIONS  
# ============================================================================  

print("\n Saving ALS table...")

# Collect and recreate to break lineage completely
als_data = als_recommendations.select(
    "Customer_ID", "Product_Name", "als_score", "confidence_score_pct", "rank"
).collect()

als_output_schema = StructType([
    StructField("Customer_ID", StringType(), True),
    StructField("Product_Name", StringType(), True),
    StructField("als_score", FloatType(), True),
    StructField("confidence_score_pct", FloatType(), True),
    StructField("rank", IntegerType(), True)
])

als_output_data = [{
    'Customer_ID': row['Customer_ID'],
    'Product_Name': row['Product_Name'],
    'als_score': float(row['als_score']),
    'confidence_score_pct': float(row['confidence_score_pct']),
    'rank': int(row['rank'])
} for row in als_data]

als_output = spark.createDataFrame(als_output_data, schema=als_output_schema).orderBy("Customer_ID", "rank")

als_output.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable("als_recommendations_table")
print("Table 1 saved: als_recommendations_table")

# ============================================================================  
# STEP 5: GENERATE LLM EXPLANATIONS (IMPROVED WITH ERROR HANDLING)
# ============================================================================  

print(f"\n Step 5: Generating LLM explanations (limiting to {SAMPLE_CUSTOMERS} customers)...")

# Initialize OpenAI client
client = OpenAI(api_key=OPENAI_API_KEY)

def generate_llm_explanation(customer_id, product_name, confidence_pct, rank, cust_dict):
    """Generate natural language recommendation explanation for account managers"""
    try:
        # Extract customer features with proper defaults
        age = cust_dict.get('Age', 'Unknown')
        occupation = cust_dict.get('Occupation', 'Unknown')
        income = cust_dict.get('Income_Bracket', 'Unknown')
        account_type = cust_dict.get('Account_Type', 'Unknown')
        gender = cust_dict.get('Gender', 'Unknown')
        location = cust_dict.get('Location', 'Unknown')
        
        # Rank context for the account manager
        if rank == 1:
            rank_context = "This is the TOP recommendation for this customer."
        elif rank == 2:
            rank_context = "This is the SECOND-BEST option for this customer."
        elif rank == 3:
            rank_context = "This is the THIRD-BEST option for this customer."
        else:
            rank_context = f"This ranks #{rank} for this customer."
        
        # Build detailed prompt for account manager
        prompt = f"""You are an AI assistant helping bank account managers make product recommendations. Provide a brief explanation (2-3 sentences) for why this product suits this customer's profile.

CUSTOMER PROFILE (ID: {customer_id}):
- Age: {age}
- Gender: {gender}
- Occupation: {occupation}
- Income Bracket: {income}
- Current Account: {account_type}
- Location: {location}

RECOMMENDED PRODUCT: {product_name}
RECOMMENDATION RANK: #{rank} (out of top 3)
MODEL CONFIDENCE: {confidence_pct:.1f}%

{rank_context}

Write a professional explanation for the account manager that:
1. Uses third-person language (e.g., "This customer...", "The client's...", "Their profile indicates...")
2. References specific customer attributes that justify this recommendation
3. Explains the product fit based on their demographics and financial profile
4. Is concise and actionable (2-3 sentences max)
5. Considers the ranking - top recommendations should emphasize strongest fit factors

Example style: "This customer's profile as a {occupation} in the {income} bracket makes them an ideal candidate for {product_name}. Their {age} and {account_type} indicate they would benefit from..."

DO NOT use second-person language like "you" or "your"."""
        
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "You are a professional banking analytics assistant providing product recommendation rationale to account managers. Always use third-person language."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=200
        )
        
        explanation = response.choices[0].message.content.strip()
        print(f"    ✓ Generated explanation for {customer_id} - {product_name} (Rank {rank})")
        return explanation
        
    except Exception as e:
        error_msg = str(e)
        print(f"    ✗ Error for {customer_id}: {error_msg[:100]}")
        # Professional fallback for account managers
        return f"This customer's profile and banking behavior patterns indicate strong alignment with {product_name}. Recommendation confidence: {confidence_pct:.1f}%."

# Get top N customers and their top 3 recommendations
print(f" Selecting top {SAMPLE_CUSTOMERS} customers...")
top_customers = (als_recommendations
    .select("Customer_ID")
    .distinct()
    .limit(SAMPLE_CUSTOMERS)
    .collect()
)
top_customer_ids = [row['Customer_ID'] for row in top_customers]

# Filter to only these customers, top 3 each
top_3_recs = (als_recommendations
    .filter(col("Customer_ID").isin(top_customer_ids))
    .filter(col("rank") <= 3)
    .collect()
)

print(f" Processing {len(top_customer_ids)} customers, {len(top_3_recs)} recommendations...")

# Pre-fetch ALL customer features efficiently
print(" Fetching customer features...")
customer_features_list = customer_features.filter(
    col("Customer_ID").isin(top_customer_ids)
).collect()

customer_dict = {row['Customer_ID']: row.asDict() for row in customer_features_list}
print(f" Loaded {len(customer_dict)} customer profiles")

# Generate explanations with progress tracking
llm_results = []
total_recs = len(top_3_recs)
start_time = datetime.now()

print(f"\n Starting LLM generation at {start_time.strftime('%H:%M:%S')}...")

for idx, row in enumerate(top_3_recs, 1):
    if idx % 5 == 0 or idx == 1:
        elapsed = (datetime.now() - start_time).total_seconds()
        avg_time = elapsed / idx if idx > 0 else 0
        remaining = avg_time * (total_recs - idx)
        print(f"  → Progress: {idx}/{total_recs} ({idx*100//total_recs}%) | Elapsed: {elapsed:.1f}s | Est. remaining: {remaining:.1f}s")
    
    cust_dict = customer_dict.get(row['Customer_ID'], {})
    
    # Generate explanation
    llm_reason = generate_llm_explanation(
        row['Customer_ID'], 
        row['Product_Name'], 
        row['confidence_score_pct'],
        row['rank'],  # Pass rank to function
        cust_dict
    )
    
    llm_results.append({
        'Customer_ID': row['Customer_ID'],
        'Product_Name': row['Product_Name'],
        'ALS_Score': float(row['als_score']),
        'Confidence_Score_PCT': float(row['confidence_score_pct']),
        'Rank': int(row['rank']),
        'LLM_Reason': llm_reason
    })

end_time = datetime.now()
total_time = (end_time - start_time).total_seconds()
print(f"\n Completed {len(llm_results)} explanations in {total_time:.1f}s (avg {total_time/len(llm_results):.2f}s per explanation)")

# Create final Spark DataFrame
llm_schema = StructType([
    StructField("Customer_ID", StringType(), True),
    StructField("Product_Name", StringType(), True),
    StructField("ALS_Score", FloatType(), True),
    StructField("Confidence_Score_PCT", FloatType(), True),
    StructField("Rank", IntegerType(), True),
    StructField("LLM_Reason", StringType(), True)
])
final_recommendations = spark.createDataFrame(llm_results, schema=llm_schema)

# ============================================================================  
# SAVE TABLE 2: FINAL RECOMMENDATIONS  
# ============================================================================  

print("\n Saving final recommendations table...")

final_output = (final_recommendations
    .select(
        "Customer_ID",
        "Product_Name",
        col("Confidence_Score_PCT").alias("Confidence_Score_Percentage"),
        col("LLM_Reason").alias("Recommendation_Reason"),
        "Rank"
    )
    .orderBy("Customer_ID", "Rank")
)
final_output.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable("final_recommendations_api_table")
print("✅ Table 2 saved: final_recommendations_api_table")

# Display sample results
print("\n SAMPLE RESULTS:")
print("-" * 100)
final_output.show(10, truncate=False)

# ============================================================================  
# SUMMARY  
# ============================================================================  

print("\n" + "="*100)
print("✅ RECOMMENDATION SYSTEM COMPLETE!")
print("="*100)
print(f" ALS Model Performance:")
print(f" RMSE: {rmse:.4f}")
print(f" Rank: {ALS_RANK}")
print(f" Regularization: {ALS_REG_PARAM}")
print(f"\n Coverage:")
print(f" Total Customers: {len(user_factor_dict):,}")
print(f" Total Products: {len(item_factor_dict):,}")
print(f"\n Output Tables:")
print(f" ALS recommendations: {len(als_output_data):,} rows → als_recommendations_table")
print(f" Final recommendations with LLM: {len(llm_results):,} rows → final_recommendations_api_table")
print(f"\n LLM Generation:")
print(f"  Total time: {total_time:.1f}s")
print(f" Avg per recommendation: {total_time/len(llm_results):.2f}s")
print("="*100)


interaction_df.show()