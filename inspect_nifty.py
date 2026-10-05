with open('c:/Nifty/Nifty.txt', 'r', encoding='utf-8', errors='ignore') as f:
    for i, line in enumerate(f):
        if 'storage_mgr.' in line:
            print(f"L{i+1}: {line.strip()[:100]}")






