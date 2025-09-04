import xarray as xr # type: ignore
import pandas as pd # type: ignore
import geopandas as gpd # type: ignore
from pathlib import Path
import re
import numpy as np # type: ignore
import os
import argparse

# Create the argument parser
parser = argparse.ArgumentParser(description="Run flooding model standardization for multiple years.")

# Define arguments
parser.add_argument("--cause", type=str, required=True, help="Cause: malaria or dengue")
parser.add_argument("--scenario", type=int, required=True, help="RCP/SSP Scenarios: 0, 75, 76")
parser.add_argument("--measure", type=str, required=True, help="Measure Counts: death or incidence")
parser.add_argument("--draw", type=int, required=True, help="Draw number: 0-99")

# Parse arguments
args = parser.parse_args()

# --------- Loading Helper Functions ------------------------------ #


# --------- Loading Helper Functions ------------------------------ #


def impute_location_ids(ds: xr.Dataset, value_var: str = "value") -> xr.Dataset:
    """
    Fast version of impute_location_ids:
    - Reassigns old location_ids to new ones.
    - Sums values into the new_id only for the few cases in IMPUTE_MAP.
    - Avoids groupby().sum(), which is a bottleneck.
    """
    IMPUTE_MAP = {
        60908: 44858,
        95069: 44858,
        94364: 44858,
    }

    ds_copy = ds.copy()

    for old_id, new_id in IMPUTE_MAP.items():
        if old_id in ds_copy.location_id.values:
            # If the new_id already exists, add the old values into it
            if new_id in ds_copy.location_id.values:
                ds_copy[value_var].loc[dict(location_id=new_id)] += \
                    ds_copy[value_var].loc[dict(location_id=old_id)]
            else:
                # If new_id does not exist, reassign the coordinate
                ds_copy = ds_copy.assign_coords(
                    location_id=xr.where(ds_copy.location_id == old_id,
                                         new_id,
                                         ds_copy.location_id)
                )
        # Drop the old_id entirely
        if old_id in ds_copy.location_id.values:
            ds_copy = ds_copy.drop_sel(location_id=old_id)

    return ds_copy

# Extra Function to load all draws
def load_draws(draw_folder_path):
    datasets = []
    for i, f in enumerate(sorted(draw_folder_path.glob("*.nc"))):
        match = re.search(r"draw_(\d+)", f.name)
        if not match:
            raise ValueError(f"Could not parse draw_id from {f.name}")
        draw_id = int(match.group(1))

        ds = xr.open_dataset(f, chunks={})  # lazy load with dask

        # If the dataset already has a "draw" dimension, just use it
        if "draw" in ds.dims:
            ds = ds.assign_coords(draw=[i])
        else:
            ds = ds.expand_dims(draw=[i])

        # Save the original draw_id as a variable (optional)
        ds = ds.assign(draw_id=("draw", [draw_id]))

        datasets.append(ds)

    return xr.concat(datasets, dim="draw")



def get_forcasted_ds(cause: str, scenario: int, measure: str, draw: int) -> xr.Dataset:
    CAUSE_MAP = {
        "malaria": "malaria.nc",
        "dengue": "ntd_dengue.nc"
    }

    main_dir = Path("/mnt/share/forecasting/data/9/future/") / measure

    scenario_and_measure_map = {
        (0, "death"): "20250709_first_sub_rcp45_climate_ref_100d_hiv_shocks_covid_all_s8_num",
        (0, "incidence"): "20250719_rcp45_first_sub_climate_ref_scen0_agg_num",
        (0, "yll"): "20250709_rcp45_first_sub_climate_ref_agg_num_restored_draws",
        (0, "yld"): "20250719_rcp45_first_sub_climate_ref_scen0_agg_num",

        (75, "death"): "20250709_first_sub_rcp26_first_sub_climate_vector_borne_diseases_100d_hiv_shocks_covid_all_s8_num",
        (75, "incidence"): "20250719_rcp26_first_sub_climate_vector_borne_diseases_scen75_agg_num",
        (75, "yll"): "20250709_rcp26_first_sub_climate_vector_borne_diseases_agg_num_restored_draws",
        (75, "yld"): "20250719_rcp26_first_sub_climate_vector_borne_diseases_scen75_agg_num",

        (76, "death"): "20250709_first_sub_rcp85_first_sub_climate_vector_borne_diseases_100d_hiv_shocks_covid_all_s8_num",
        (76, "incidence"): "20250719_rcp85_first_sub_climate_vector_borne_diseases_scen76_agg_num",
        (76, "yll"): "20250709_rcp85_first_sub_climate_vector_borne_diseases_agg_num_restored_draws",
        (76, "yld"): "20250719_rcp85_first_sub_climate_vector_borne_diseases_scen76_agg_num",

        }

    if (scenario, measure) not in scenario_and_measure_map:
        raise ValueError("Invalid scenario ID and measure combination.")

    dataset_name = scenario_and_measure_map[(scenario, measure)]
    file_path = main_dir / dataset_name / CAUSE_MAP[cause]

    if not file_path.exists():
        raise FileNotFoundError(f"The file {file_path} does not exist.")

    ds = xr.open_dataset(file_path)

    ds = ds.where(ds["draw"] == draw, drop=True)

    if "draws" in ds.data_vars:
        ds = ds.rename_vars({"draws": "value"})

    return ds



def get_predicted_ds(cause: str, scenario: int, measure: str, draw: int) -> xr.Dataset:
    """
    Predicted admin 2 values from Bobby.
    """
    SCENARIO_MAP = {
        0: "ssp245",
        75: "ssp126",
        76: "ssp585",
    }

    MEASURE_MAP = {
        "death": "mortality",
        "yll": "mortality",
        "incidence": "incidence",
        "yld": "incidence",
    }

    main_dir = Path("/mnt/team/rapidresponse/pub/malaria-denv/deliverables/2025_08_26_admin_2_counts/input/")

    scenario_second_name = SCENARIO_MAP[scenario]
    measure_second_name = MEASURE_MAP[measure]

    if cause == "malaria":
        draw_folder = f"as_cause_{cause}_measure_{measure_second_name}_metric_count_ssp_scenario_{scenario_second_name}_dah_scenario_Baseline"
    elif cause == "dengue":
        draw_folder = f"as_cause_{cause}_measure_{measure_second_name}_metric_count_ssp_scenario_{scenario_second_name}"
    else:
        warning = f"Unknown cause: {cause}, only dengue or malaria available."
        print(f"{warning}")

    draw_folder_path = main_dir / draw_folder

    if not draw_folder_path.exists():
        raise FileNotFoundError(f"The folder {draw_folder_path} does not exist.")
    
    # merge all draw nc files into a single ds
    filename = f"draw_{draw}.nc"
    ds = xr.open_dataset(draw_folder_path / filename, chunks={})  # lazy load with dask

    if "draw" not in ds.dims:
        ds = ds.expand_dims("draw")

    ds = ds.assign_coords(draw=[draw])

    # assign scenario coordinate
    ds = ds.expand_dims("scenario").assign_coords(scenario=[scenario])

    # rename data variable to value
    ds = ds.rename({"val": "value"})

    # cast all coords to int64
    for coord in ds.coords:
        ds[coord] = ds[coord].astype("int64")

    return ds

def load_in_hierarchy_dataset() -> None:
    """
    Loads full LSAE Hierarchy 2023 LSAE_1209
    """

    main_dir = Path("/mnt/team/rapidresponse/pub/malaria-denv/deliverables/2025_08_26_admin_2_counts/")
    file_name = "full_hierarchy_2023_lsae_1209.nc"

    ds = xr.open_dataset(main_dir / file_name)

    # subset to admin 2
    ds = ds.where(ds["level"] == 5, drop=True)

    for coord in ds.coords:
        ds[coord] = ds[coord].astype("int64")

    # change parentId to int64
    ds["parent_id"] = ds["parent_id"].astype("int64")

    return ds

def subset_admin1_to_admin2_dims(ds_admin1: xr.Dataset, ds_admin2: xr.Dataset) -> xr.Dataset:
    """
    Subset ds_admin1 to only the age_group_id and sex_id combinations present in ds_admin2.
    This ensures alignment for raking calculations.
    """
    # Determine intersecting age_group_id and sex_id
    age_ids = [a for a in ds_admin1.age_group_id.values if a in ds_admin2.age_group_id.values]
    sex_ids = [s for s in ds_admin1.sex_id.values if s in ds_admin2.sex_id.values]

    # Subset ds_admin1
    ds_admin1_subset = ds_admin1.sel(age_group_id=age_ids, sex_id=sex_ids)

    return ds_admin1_subset


# ---------- Main Helper Functions ---------------------------------#

def subset_ds_to_admin2_locations(hierarchy_ds: xr.Dataset, ds: xr.Dataset) -> xr.Dataset:
    """
    Subset dataset to only the intersection of location_ids in the hierarchy_ds
    and location_ids in the dataset.
    Drops unmatched IDs from both ends.
    """
    admin2_ids = set(hierarchy_ds.location_id.values)
    ds_ids = set(ds.location_id.values)

    # Intersection only
    common_ids = sorted(admin2_ids & ds_ids)

    return ds.sel(location_id=common_ids, drop=True)


def attach_hierarchy(ds: xr.Dataset, hierarchy_ds: xr.Dataset) -> xr.Dataset:
    """
    Attach hierarchy information to the admin2 predictions using the full hierarchy dataset.
    Keeps only location_ids present in both ds and hierarchy_ds.
    Retains all hierarchy metadata (parent_id, names, etc.).
    """

    # --- Step 0: restrict to common IDs --- #
    common_ids = sorted(set(ds.location_id.values) & set(hierarchy_ds.location_id.values))
    ds_admin2 = ds.sel(location_id=common_ids, drop=True)
    hierarchy_sub = hierarchy_ds.sel(location_id=common_ids, drop=True)

    # --- Step 1: merge hierarchy metadata into ds --- #
    # This will preserve all variables from hierarchy_ds (parent_id, names, etc.)
    ds_admin2 = xr.merge([ds_admin2, hierarchy_sub])

    return ds_admin2

def split_ds_admin1(ds_admin2: xr.Dataset, ds_admin1: xr.Dataset) -> tuple[xr.Dataset, xr.Dataset]:
    """
    Split admin1 dataset into:
      1. locations that correspond to parent_ids in ds_admin2
      2. locations that do not correspond to parent_ids in ds_admin2

    Uses direct indexing for speed.
    """
    # Identify parent_ids in admin2 that exist in admin1 location_id
    common_ids = list(set(ds_admin2["parent_id"].values) & set(ds_admin1["location_id"].values))
    
    # Directly select with .sel() for fast indexing
    ds_admin1_with_parent_id = ds_admin1.sel(location_id=common_ids)
    
    # Select remaining locations using difference
    remaining_ids = list(set(ds_admin1["location_id"].values) - set(common_ids))
    ds_admin1_without_parent_id = ds_admin1.sel(location_id=remaining_ids)

    return ds_admin1_with_parent_id, ds_admin1_without_parent_id


def split_ds_admin2(ds_admin2: xr.Dataset, ds_admin1: xr.Dataset) -> tuple[xr.Dataset, xr.Dataset]:
    """
    Split admin 2 dataset into admin 2 units with parent ids in admin 1 dataset and not.
    """

    common_ids = np.array(list(set(ds_admin2["parent_id"].values) & set(ds_admin1["location_id"].values)))
    mask = ds_admin2["parent_id"].isin(common_ids)

    # Keep rows where parent_id is in ds_admin1
    ds_admin2_with_parent = ds_admin2.where(mask, drop=True)

    # Keep rows where parent_id is not in ds_admin1
    ds_admin2_without_parent = ds_admin2.where(~mask, drop=True)


    return ds_admin2_with_parent, ds_admin2_without_parent

def sum_and_align_admin2_totals(
    ds_admin2: xr.Dataset,
    ds_admin1: xr.Dataset,
    value_var: str = "value"
) -> xr.DataArray:
    # Ensure parent_id is coordinate
    if "parent_id" not in ds_admin2.coords:
        ds_admin2 = ds_admin2.assign_coords(parent_id=ds_admin2["parent_id"])

    # Sum only over location_id
    admin2_sums = ds_admin2[value_var].groupby("parent_id").sum(dim="location_id")
    admin2_sums = admin2_sums.rename({"parent_id": "location_id"})

    # Keep admin1 location_id coordinate
    admin1_ids = ds_admin1["location_id"].values

    # Reindex admin2_sums to match admin1
    admin2_sums_aligned = admin2_sums.reindex(location_id=admin1_ids)

    factor = xr.where(
        (admin2_sums_aligned == 0) | (ds_admin1 == 0),
        1.0,
        ds_admin1 / xr.where(admin2_sums_aligned == 0, 1, admin2_sums_aligned)
    )

    return factor




def broadcast_factor_to_admin2(
    ds_admin2: xr.Dataset,
    factor: xr.Dataset,
    value_var: str = "value"
) -> xr.DataArray:
    """
    Broadcast raking factors (per parent_id/admin1) to admin2 units.
    Uses xarray broadcasting and alignment, so it's memory-efficient with dask.
    """

    # Ensure parent_id is a coordinate
    if "parent_id" not in ds_admin2.coords:
        ds_admin2 = ds_admin2.assign_coords(parent_id=ds_admin2["parent_id"])

    # Extract factor DataArray
    if isinstance(factor, xr.Dataset):
        var_name = list(factor.data_vars)[0]
        factor_da = factor[var_name]
    else:
        factor_da = factor

    # Rename factor dimension for alignment
    factor_da = factor_da.rename({"location_id": "parent_id"})

    # Align factor to admin2 parent_id
    factor_broadcasted = factor_da.sel(parent_id=ds_admin2["parent_id"])

    # Multiply by admin2 values
    raked_values = ds_admin2[value_var] * factor_broadcasted

    return raked_values



def build_raked_dataset(ds_admin2: xr.Dataset, raked_values: xr.DataArray, value_var: str = "value") -> xr.Dataset:
    """
    Build the final raked dataset for admin2, by assigning the raked values to the dataset.

    """

    # Ensure parent_id is a coordinate
    if "parent_id" not in ds_admin2.coords:
        ds_admin2 = ds_admin2.assign_coords(parent_id=ds_admin2["parent_id"])
        
    # --- Step 6: build final dataset ---
    ds_admin2_raked = ds_admin2.copy()
    ds_admin2_raked[value_var] = raked_values

    # assign parent_id as data variable
    ds_admin2_raked = ds_admin2_raked.reset_coords("parent_id", drop=False)

    return ds_admin2_raked

def merge_raked_and_unraked_admin2_dask(
    raked_ds: xr.Dataset, ds_admin2_without_parent: xr.Dataset
) -> xr.Dataset:
    """
    Merge raked and unraked admin2 datasets efficiently using dask.
    
    Assumes:
    - Both datasets are dask-backed.
    - Only 'location_id' differs; all other coordinates are identical.
    - location_id values are disjoint.
    """
    # Ensure both datasets are dask-backed
    if not raked_ds.chunks:
        raked_ds = raked_ds.chunk()
    if not ds_admin2_without_parent.chunks:
        ds_admin2_without_parent = ds_admin2_without_parent.chunk()

    # Concatenate along location_id (lazy dask operation)
    merged_ds = xr.concat([raked_ds, ds_admin2_without_parent], dim="location_id")

    return merged_ds

def drop_data_variables(merged_ds: xr.Dataset) -> xr.Dataset:
    """
    Drop all data variables except 'value' from the dataset.
    """
    data_vars_to_keep = ['value']
    data_vars_to_drop = [var for var in merged_ds.data_vars if var not in data_vars_to_keep]
    return merged_ds.drop_vars(data_vars_to_drop)



def save_raked_dataset_optimized(
    cause: str,
    scenario: int,
    measure: str,
    draw: int,
    merged_ds: xr.Dataset,
) -> None:
    """
    Save the raked admin2 dataset to NetCDF with faster I/O.
    Optimized for large, merged datasets.
    """
    SCENARIO_MAP = {0: "ssp245", 75: "ssp126", 76: "ssp585"}
    MEASURE_MAP = {"death": "mortality", "incidence": "incidence", "yll" : "yll", "yld": "yld"}

    scenario_name = SCENARIO_MAP[scenario]
    measure_name = MEASURE_MAP[measure]

    # --- Output directory ---
    out_dir = Path("/mnt/team/rapidresponse/pub/malaria-denv/deliverables/2025_08_26_admin_2_counts/output/")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_dir.chmod(0o775)

    # --- Build subdir ---
    if cause == "malaria":
        dirname = (
            f"as_cause_{cause}_measure_{measure_name}_metric_count_"
            f"ssp_scenario_{scenario_name}_dah_scenario_Baseline_raked"
        )
    else:  # dengue
        dirname = (
            f"as_cause_{cause}_measure_{measure_name}_metric_count_"
            f"ssp_scenario_{scenario_name}_raked"
        )
    out_dir = out_dir / dirname
    out_dir.mkdir(parents=True, exist_ok=True)

    # --- Filename ---
    outfile = out_dir / f"draw_{int(draw)}.nc"

    # --- Encoding ---
    # Use faster compression (or none, if speed > size matters)
    encoding = {
        var: {
            "zlib": True,
            "complevel": 0,
            "dtype": "float32",
            "chunksizes": tuple(s[0] for s in merged_ds[var].chunks),  
        }
        for var in merged_ds.data_vars
    }
    # --- Save ---
    # h5netcdf engine is much faster than netcdf4 for large outputs
    merged_ds.to_netcdf(
        outfile,
        engine="h5netcdf",
        encoding=encoding,
        compute=True,
    )
    
    # --- Fix permissions ---
    os.chmod(outfile, 0o775)

    print(f"[✓] Saved: {outfile}")

def main_raking_function(
    cause: str,
    scenario: int,
    measure: str,
    draw: int,
    value_var: str = "value"
) -> xr.Dataset:
    """
    Main function to rake admin 2 predictions to admin 1.
    """

    # Step 1: Load and prepare data inputs
    ds_admin1 = get_forcasted_ds(cause, scenario, measure, draw)
    ds_admin2 = get_predicted_ds(cause, scenario, measure, draw)
    ds_admin2 = impute_location_ids(ds_admin2, value_var=value_var)
    ds_admin2 = ds_admin2.transpose(*ds_admin1.dims)

    hierarchy = load_in_hierarchy_dataset()

    # Step 2: Ensure ds_admin1 has the same coordinates as ds_admin2 in terms of age and sex
    ds_admin1 = subset_admin1_to_admin2_dims(ds_admin1, ds_admin2)

    # Step 3: Ensure only admin 2 locations are present
    ds_admin2 = subset_ds_to_admin2_locations(hierarchy, ds_admin2)

    # Step 4: Attach hierarchy information
    ds_admin2 = attach_hierarchy(ds_admin2, hierarchy)

    # Step 5: Split datasets into those with and without parent_id in admin1
    ds_admin2_with_parent, ds_admin2_without_parent = split_ds_admin2(ds_admin2, ds_admin1)
    ds_admin1_with_parent, _ = split_ds_admin1(ds_admin2, ds_admin1)

    # Step 6: Sum and align admin2 totals with admin1 totals
    factor = sum_and_align_admin2_totals(ds_admin2_with_parent, ds_admin1_with_parent, value_var)

    # Step 7: Broadcast factor to admin2 dataset
    raked_values = broadcast_factor_to_admin2(ds_admin2_with_parent, factor, value_var)

    # Step 8: Build final raked dataset
    ds_admin2_raked = build_raked_dataset(ds_admin2_with_parent, raked_values, value_var)

    # Step 9: Remerge raked admin 2 and the original admin 2 without parent id
    merged_ds = merge_raked_and_unraked_admin2_dask(ds_admin2_raked, ds_admin2_without_parent)

    # Step 10: Drop data variables
    merged_ds = drop_data_variables(merged_ds)

    # Step 11: Save output
    save_raked_dataset_optimized(cause, scenario, measure, draw, merged_ds)

    return merged_ds


# Call the function with parsed arguments
main_raking_function(
    cause=args.cause,
    scenario=args.scenario,
    measure=args.measure,
    draw=args.draw,
    value_var="value"
    )
