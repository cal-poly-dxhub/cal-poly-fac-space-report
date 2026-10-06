# Where each column comes from

A report row is one **facility**: every `PropertyDetails` row sharing a
`FreeString13`. That value is a `Property` syscode pointing at one member, the
**anchor**. Descriptive columns come from the anchor alone. Areas sum across
every member.

`->` means follow a reference into another table.

| Column | Path |
| --- | --- |
| NUM | anchor `Property.Code`, digits before the hyphen. Drop a campus prefix: `SC-024-0` is `024` |
| SFX | anchor `Property.Code`, character after the hyphen. `0` or none prints `-` |
| FAC NAME | anchor `Property.FreeString11` |
| CATEGORY CODE / DESC | anchor's `PropertyDetails.FreeString8` -> `BaseCodes` -> Code, Name |
| STATUS CODE / DESC | anchor `Property.FreeString5` -> `BaseCodes` -> Code, Name |
| OWNER CODE / DESC | anchor's `PropertyDetails.FreeString9` -> `BaseCodes` -> Code, Name |
| GSF | sum `PropertyDetails.GrossFloorArea` over every member |
| ASF | sum `SpaceUsage.FloorArea` over every member's spaces that have a CSU SpCd |
| EFFC | ASF / GSF, blank if either is blank or zero |
| COMPL DATE | anchor `Property.PurchaseDate`, printed `MM-YYYY` |
| Center (section header) | anchor `Property.FreeString7` -> `BaseCodes` -> Code, Name |

## ASF filter

Drop a `SpaceUsage` row whose `FreeString1` (CSU SpCd, space type) is blank.
`FreeString1` -> `BaseCodes` (group `SPACE_SPCD`) gives the space type's name,
e.g. `0095 Dorm-Single`, but only whether it is populated matters for the sum.

This replaced an older rule that dropped rows whose `SpaceStandard`, or its
parent, had code `000` "Nonassignable". The report no longer reads
`SpaceStandard`.

## Which rows count

Every filter runs against the reference date. Both bounds inclusive, empty
`EndDate` means still in force.

    PropertyDetails, all rows              830
      FreeString13 set                     432
      effective at the reference date      410
      FreeString14 -> BaseCodes -> 'Y'     406
      grouped by FreeString13              187 facilities

No center filter. A blank CSU center must not exclude a property.

## Business names

Names for the slots, from the requirements doc.

| Slot | Called |
| --- | --- |
| `PropertyDetails.FreeString13` | CSU facility |
| `PropertyDetails.FreeString14` | Reported to Chancellors Office |
| `PropertyDetails.FreeString8` | CSU use type |
| `PropertyDetails.FreeString9` | CSU Ownership type |
| `Property.FreeString5` | Master plan status |
| `Property.FreeString7` | CSU center |
| `Property.FreeString11` | Full name |
| `SpaceUsage.FreeString1` | CSU SpCd (space type) |
| `Area 14` / `Area 15` | CSU GSF / CSU ASF. Planon's own export uses these as the GSF and ASF headers |
