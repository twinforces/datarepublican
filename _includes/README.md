# Jekyll Includes

This directory contains reusable components for the DataRepublican site.

## Search Metadata

The `search-metadata.html` include provides functionality to add proper SEO metadata for search pages, including:

1. Dynamic title updates based on search queries
2. Canonical URL generation for search results
3. H1 tag updates to reflect search queries
4. JSON-LD structured data for search pages

### Usage

Add this include to any page that has search functionality:

```html
{% include search-metadata.html 
  base_title="Your Page Title"
  base_url="https://datarepublican.com/your-page/"
  search_id="pageTitle"
  param_names='["param1", "param2"]'
%}
```

### Parameters

- `base_title`: The base title of the page (required)
- `base_url`: The canonical base URL without query parameters (required)
- `search_id`: The ID of the h1 element to update (default: "pageTitle")
- `param_names`: Array of URL parameter names to check (default: ["q", "query", "search"])

### Implementation Steps

1. Add an ID to your page's h1 element:
   ```html
   <h1 id="pageTitle">Your Page Title</h1>
   ```

2. Include the search-metadata.html file with appropriate parameters:
   ```html
   {% include search-metadata.html 
     base_title="Your Page Title"
     base_url="https://datarepublican.com/your-page/"
   %}
   ```

3. For form submissions, make sure your form inputs have name attributes that match the parameter names:
   ```html
   <input type="text" name="q" id="searchInput">
   ```

4. For JavaScript-based search, call the updateSearchMetadata function after updating the URL:
   ```javascript
   // After updating URL parameters
   updateSearchMetadata(
     new URLSearchParams(window.location.search),
     "Your Page Title", 
     "https://datarepublican.com/your-page/", 
     "pageTitle"
   );
   ```

### Example

See `officers/index.html` for a complete implementation example. 