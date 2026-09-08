-- 种子数据：主流币种 + 模型单价占位
INSERT INTO symbols (exchange, symbol, base_asset, quote_asset, status, is_favorite) VALUES
    ('binance', 'BTCUSDT', 'BTC', 'USDT', 'TRADING', TRUE),
    ('binance', 'ETHUSDT', 'ETH', 'USDT', 'TRADING', TRUE),
    ('binance', 'SOLUSDT', 'SOL', 'USDT', 'TRADING', TRUE),
    ('binance', 'BNBUSDT', 'BNB', 'USDT', 'TRADING', TRUE),
    ('binance', 'XRPUSDT', 'XRP', 'USDT', 'TRADING', TRUE),
    ('binance', 'DOGEUSDT', 'DOGE', 'USDT', 'TRADING', FALSE),
    ('binance', 'ADAUSDT', 'ADA', 'USDT', 'TRADING', FALSE),
    ('binance', 'AVAXUSDT', 'AVAX', 'USDT', 'TRADING', FALSE),
    ('binance', 'LINKUSDT', 'LINK', 'USDT', 'TRADING', FALSE),
    ('binance', 'MATICUSDT', 'MATIC', 'USDT', 'TRADING', FALSE)
ON CONFLICT (exchange, symbol) DO NOTHING;

-- 模型单价（USD / 1k tokens，占位值，需在 /settings 校准）
INSERT INTO llm_costs (provider, model, prompt_per_1k, completion_per_1k, currency) VALUES
    ('openai', 'gpt-4o-mini', 0.00015, 0.0006, 'USD'),
    ('openai', 'gpt-4o',      0.0025,  0.01,   'USD'),
    ('openai', 'gpt-4.1-mini',0.0004,  0.0016, 'USD'),
    ('deepseek', 'deepseek-chat', 0.00014, 0.00028, 'USD'),
    ('anthropic', 'claude-3-5-sonnet', 0.003, 0.015, 'USD')
ON CONFLICT DO NOTHING;
