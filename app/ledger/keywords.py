"""Merchant keyword lists (Indian usage) used by categories.py.

Matched against the merchant / payee name only, never the SMS boilerplate.
categories.py extends some of these with extra words and splits Travel from Transport.
"""

FOOD_DINING = {
    "swiggy", "zomato", "eatsure", "eatfit", "dominos", "pizza hut", "kfc",
    "mcdonalds", "burger king", "starbucks", "haldiram", "barbeque", "cafe",
    "biryani", "chai point", "chaayos", "third wave", "blue tokai",
    "pizza", "restaurant", "food", "dining", "faasos", "box8", "freshmenu",
    "dunkin", "subway", "wow momo", "behrouz", "oven story", "fassos",
}

GROCERIES = {
    "bigbasket", "blinkit", "grofers", "zepto", "instamart", "dunzo daily",
    "dmart", "d mart", "dmart ready", "jiomart", "reliance smart",
    "reliance fresh", "reliance retail", "milkbasket", "supr daily",
    "spencer", "more supermarket", "star bazaar", "nature basket",
    "amazon fresh", "grocery",
}

TRANSPORT = {
    "uber", "ola", "olacabs", "rapido", "meru", "namma yatri",
    "irctc", "ixigo", "confirmtkt", "makemytrip", "goibibo", "yatra",
    "cleartrip", "redbus", "abhibus", "railyatri", "chalo",
    "shell", "hp petrol", "indianoil", "bharat petroleum", "iocl", "bpcl",
    "hpcl", "petrol", "fuel", "parking", "fastag", "paytm fastag", "toll",
    "metro", "dmrc", "bmtc", "brts", "auto", "cab",
}

SHOPPING = {
    "amazon", "amzn", "flipkart", "myntra", "ajio", "nykaa", "meesho",
    "tatacliq", "tata cliq", "snapdeal", "shopclues", "firstcry",
    "purplle", "boat lifestyle", "boat.", "lenskart", "titan", "tanishq",
    "croma", "reliance digital", "vijay sales", "lifestyle stores",
    "westside", "shoppers stop", "pantaloons", "max fashion", "h&m",
    "uniqlo", "decathlon", "ikea", "urban ladder", "pepperfry",
    "the man company", "mamaearth", "wow skin", "nykaa fashion", "shein",
}

SHOPPING_EXCLUDE = {"amazon pay", "amazon fresh", "amazon prime video"}

UTILITIES = {
    "airtel", "jio recharge", "reliance jio", "vodafone", "vi ",
    "bsnl", "mtnl", "act broadband", "hathway", "tikona", "excitel",
    "tata power", "adani electricity", "bses", "torrent power",
    "mahadiscom", "kseb", "tneb", "bescom", "reliance energy",
    "gail", "indraprastha gas", "mahanagar gas", "adani gas", "gas bill",
    "delhi jal", "bwssb", "mjp", "water bill",
    "electricity", "postpaid", "prepaid", "recharge",
    "chatgpt", "openai", "anthropic", "claude", "notion", "figma",
    "spotify", "netflix", "prime video", "hotstar", "sony liv", "zee5",
    "youtube premium", "apple.com/bill", "icloud", "google one",
    "microsoft 365", "office 365", "github", "adobe", "canva pro",
}

RENT = {
    "nobroker", "rentomojo", "furlenco", "housing.com", "magicbricks",
    "rent payment", "rent for", "monthly rent", "house rent",
}

HEALTH = {
    "apollo pharmacy", "1mg", "netmeds", "pharmeasy", "medlife",
    "practo", "portea", "cure fit", "curefit", "cult fit", "cultfit",
    "healthkart", "medplus", "wellness forever", "truemeds",
    "hospital", "clinic", "diagnostic", "pathology", "medicine",
    "manipal", "fortis", "apollo", "max healthcare", "aiims",
}

INSURANCE = {
    "lic", "life insurance corp", "hdfc life", "sbi life", "icici pru",
    "icici prudential", "max life", "bajaj allianz", "tata aia",
    "kotak life", "aditya birla sun life", "pnb metlife", "reliance nippon",
    "insurance", "policy premium", "premium payment", "star health",
    "niva bupa", "care health", "religare",
}

INVESTMENTS = {
    "groww", "zerodha", "kite.zerodha", "upstox", "angel one", "angelone",
    "smallcase", "kuvera", "coinbyte", "coindcx", "wazirx", "coinswitch",
    "mutual fund", "mf sip", "sip investment", "sip debit", "systematic investment",
    "nps ", "nps contribution", "ppf", "epf", "elss", "nsdl",
    "cams", "kfintech", "kfin", "sbi mutual", "hdfc amc", "icici prudential mf",
    "axis mutual", "nippon india mutual", "aditya birla sun life amc",
    "quant mutual", "parag parikh", "zerodha kite", "grow trading",
    "purchase of shares", "equity purchase", "stock purchase",
    "sip:", "invest", "acegain", "indmoney", "bajaj finserv direct",
}

BANK_CHARGES = {
    "gst on", "sms charges", "amc charge", "atm charge", "atm withdrawal charge",
    "chq bounce", "cheque bounce", "ecs return", "insufficient bal charge",
    "annual fee", "renewal fee", "processing fee",
}

SALARY = {
    "salary", "payroll", "sal cr", "sal-cr", "monthly salary",
    "salary credit", "credited by salary", "salary for the month",
    "salary of", "sal for",
}

INCOME_INTEREST = {
    "interest credited", "int credit", "int for", "sb int", "fd interest",
    "term deposit interest", "dividend", "div paid",
}
