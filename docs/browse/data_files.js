export const DATA_FILES = {
  dbVersion: 1087896,
  files: [
    {
    status: "Loading Charities",
    baseFile: "./tsv_chunks/charities_chunk_",
    tsvFilePrefix: "charities_chunk_",
    type: "charities",
    chunkCount: 92
  },
    {
    status: "Loading 501 Grants",
    baseFile: "./tsv_chunks/grants_final_chunk_",
    tsvFilePrefix: "grants_final_chunk_",
    type: "grants",
    chunkCount: 17, grantType: "regular"
  },
    {
    status: "Loading Private Foundation Grants",
    baseFile: "./tsv_chunks/grants.pf_chunk_",
    tsvFilePrefix: "grants.pf_chunk_",
    type: "grants",
    chunkCount: 6, grantType: "private"
  }
  ]
};
