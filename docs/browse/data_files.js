const FILES_10M = [
  {
    "status": "Loading Charities",
    "baseFile": "/browse/tsv_chunks/charities_chunk_",
    "tsvFilePrefix": "charities_chunk_",
    "type": "charities",
    "chunkCount": 11
  },
  {
    "status": "Loading Grants",
    "baseFile": "/browse/tsv_chunks/grants_final_chunk_",
    "tsvFilePrefix": "grants_final_chunk_",
    "type": "grants",
    "chunkCount": 59,
    "grantType": "regular"
  }
];

export const DATA_FILES = {
  dbVersion: "2026-09-03T05:52:46Z",
  defaultBand: "10M",
  bands: [
    {
      id: "10M",
      label: "$10M",
      threshold: 10000000,
      nodes: 103746,
      grants: 586476,
      dollars: 2405409249784,
      edges: 558647,
      files: FILES_10M,
    },
    {
      id: "1M",
      label: "$1M",
      threshold: 1000000,
      nodes: 371236,
      grants: 1698597,
      dollars: 2574763247168,
      edges: 1618824,
      files: [
        {
          status: "Loading Charities ($1M)",
          baseFile: "/browse/tsv_chunks/1m/charities_chunk_",
          tsvFilePrefix: "charities_chunk_",
          type: "charities",
          chunkCount: 38,
        },
        {
          status: "Loading Grants ($1M)",
          baseFile: "/browse/tsv_chunks/1m/grants_final_chunk_",
          tsvFilePrefix: "grants_final_chunk_",
          type: "grants",
          chunkCount: 170,
          grantType: "regular",
        },
      ],
    },
    {
      id: "all",
      label: "All",
      threshold: 1,
      nodes: 2641709,
      grants: 5627968,
      dollars: 2600747385432,
      edges: 5604343,
      files: [
        {
          status: "Loading Charities (All)",
          baseFile: "/browse/tsv_chunks/all/charities_chunk_",
          tsvFilePrefix: "charities_chunk_",
          type: "charities",
          chunkCount: 265,
        },
        {
          status: "Loading Grants (All)",
          baseFile: "/browse/tsv_chunks/all/grants_final_chunk_",
          tsvFilePrefix: "grants_final_chunk_",
          type: "grants",
          chunkCount: 563,
          grantType: "regular",
        },
      ],
    },
  ],
  files: FILES_10M,
};
