{
  "updated": "2026-09-15 18:12:52",
  "chunked_complete": [
    "plantnet_300K.zip",
    "CID-InChI-Key.gz",
    "CID-SMILES.gz"
  ],
  "chunked_partial": {
    "DF20-train_val.tar.gz": "109/108",
    "latest-mediainfo.json.bz2": "57/57",
    "inaturalist-open-data-20260827.tar.gz": "33/33",
    "commonswiki-latest-image.sql.gz": "18/18"
  },
  "single_files": [
    {
      "name": "MetObjects.csv",
      "bytes": 317650992,
      "license": "CC0",
      "source": "github.com/metmuseum/openaccess (LFS)",
      "note": "49.2万藏品记录，isPublicDomain 过滤后取图"
    },
    {
      "name": "DF20-metadata.zip",
      "bytes": 29089651,
      "license": "CC BY",
      "source": "ptak.felk.cvut.cz",
      "note": "DF20 观测元数据（学名/GBIF）"
    }
  ],
  "openimages_annotations": [
    {
      "name": "oidv6-train-annotations-bbox.csv",
      "bytes": 2258447590
    },
    {
      "name": "oidv7-test-annotations-human-imagelabels.csv",
      "bytes": 93606939
    },
    {
      "name": "oidv7-train-annotations-human-imagelabels.csv",
      "bytes": 2735816020
    }
  ],
  "smithsonian_batches": "46/46 (每批300片tar.gz, 48.35GB全量)",
  "pending_blocked": {
    "WIT全量TSV(27GB)": "GCS 对节点d限速0，待恢复或换机",
    "ImageNet-21K(1.1-1.3TB)": "HF gated，需 token 接受条款",
    "VisualSem(31GB)": "文件密码，需机构邮箱向作者申请",
    "BIOSCAN/HPA/OI图片": "按概念清单取，等 concept_xref 关联产出",
    "Rijksmuseum/Europeana": "需免费 API key",
    "OI val/test bbox 标注": "URL 403，需从 V7 下载页取正确链接"
  },
  "fusion_complete": {
    "updated": "2026-09-17 11:56:46",
    "edges_total": 2793075,
    "shards": {
      "df20": {
        "rows": 202336,
        "concepts": 878,
        "new_blobs": 184006,
        "relation": "definitional"
      },
      "plantnet": {
        "rows": 275200,
        "concepts": 740,
        "new_blobs": 275200,
        "relation": "definitional"
      },
      "sdc_attach": {
        "rows": 2288880,
        "new_blobs": 0,
        "relation": "depicts_part"
      },
      "pubchem": {
        "rows": 26659,
        "concepts": 24036,
        "new_blobs": 26659,
        "double_bridge": 25998
      }
    },
    "ledger_dir": "demiwtg-data/datasets/demiwtg/kb/qid_images_ext/",
    "smithsonian_media_discovery": "图本体在同桶 media/ 前缀可S3直取;记录为JSONL;ISD无Getty键须名字匹配",
    "handover": "demiwtg-data/HANDOVER_TRAINING_MACHINE.md"
  }
}