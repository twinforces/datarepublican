#!/usr/bin/env python3

import argparse
import string
from collections import Counter

# Set up argument parser
parser = argparse.ArgumentParser(description="Process TSV file to count words in 'filer_name' column and output to CSV.")
parser.add_argument('--input-file', required=True, help='Path to the input TSV file')
parser.add_argument('--output-file', required=True, help='Path to the output CSV file')
args = parser.parse_args()

# Define stop words (NLTK English stop words, plus '&' and '-')
stop_words = set([
    'i', 'me', 'my', 'myself', 'we', 'our', 'ours', 'ourselves', 'you', 'your', 'yours',
    'yourself', 'yourselves', 'he', 'him', 'his', 'himself', 'she', 'her', 'hers',
    'herself', 'it', 'its', 'itself', 'they', 'them', 'their', 'theirs', 'themselves',
    'what', 'which', 'who', 'whom', 'this', 'that', 'these', 'those', 'am', 'is', 'are',
    'was', 'were', 'be', 'been', 'being', 'have', 'has', 'had', 'having', 'do', 'does',
    'did', 'doing', 'a', 'an', 'the', 'and', 'but', 'if', 'or', 'because', 'as', 'until',
    'while', 'of', 'at', 'by', 'for', 'with', 'about', 'against', 'between', 'into',
    'through', 'during', 'before', 'after', 'above', 'below', 'to', 'from', 'up', 'down',
    'in', 'out', 'on', 'off', 'over', 'under', 'again', 'further', 'then', 'once', 'here',
    'there', 'when', 'where', 'why', 'how', 'all', 'any', 'both', 'each', 'few', 'more',
    'most', 'other', 'some', 'such', 'no', 'nor', 'not', 'only', 'own', 'same', 'so',
    'than', 'too', 'very', 's', 't', 'can', 'will', 'just', 'don', 'should', 'now',
    '&', '-','st','llc','local'
])

# Open and read the TSV file
with open(args.input_file, 'r') as tsvfile:
    # Read header and find column index
    header = tsvfile.readline().strip().split('\t')
    try:
        col_index = header.index('filer_name')
    except ValueError:
        raise ValueError("Column 'filer_name' not found in header.")

    # Collect all words from the filer_name column, stripping punctuation and normalizing to lowercase
    all_words = []
    for line in tsvfile:
        fields = line.strip().split('\t')
        if len(fields) > col_index:
            name = fields[col_index].strip()
            if name:  # Skip empty values
                # Split, strip punctuation, lowercase, and skip empty words
                words = [w.strip(string.punctuation).lower() for w in name.split() if w.strip(string.punctuation)]
                all_words.extend(words)

# Count word frequencies
word_counts = Counter(all_words)

# Sort by weight descending
sorted_counts = sorted(word_counts.items(), key=lambda x: x[1], reverse=True)

# Write to CSV using simple string joins without quoting, with words in title case, skipping stop words and "inc"
with open(args.output_file, 'w') as csvfile:
    csvfile.write('weight,word\n')
    for word, count in sorted_counts:
        if word in stop_words or word == 'inc':
            continue  # Skip stop words and "inc"
        title_word = str(word).title()  # Ensure string and convert to title case for output
        line = ','.join([str(count), title_word]) + '\n'
        csvfile.write(line)

print(f"CSV file '{args.output_file}' generated successfully.")