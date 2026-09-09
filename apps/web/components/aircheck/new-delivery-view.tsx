'use client';

import { useEffect, useState } from 'react';
import { FileArchive, FileCheck2, Package, Shield } from 'lucide-react';

import { Button } from '@/components/ui/button';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { createRun, getMetaStates, type ProfileMeta } from '@/lib/api';
import { navigateTo } from '@/lib/navigate';

// Truthful fallback matching the two valid Northstar profiles. The receipt is
// refined with the live /meta/states projection once it loads.
const FALLBACK_PROFILES: ProfileMeta[] = [
  {
    profile_id: 'northstar_broadcast_master_v1',
    profile_name: 'Northstar Broadcast Master',
    description:
      '15 normalized checks · strict broadcast delivery · fictional fixture v1',
    checks_count: 15,
  },
  {
    profile_id: 'northstar_digital_preview_v1',
    profile_name: 'Northstar Digital Preview',
    description:
      '13 normalized checks · digital streaming preview · fictional fixture v1',
    checks_count: 13,
  },
];

type PackageType = 'hero' | 'clean';

export function NewDeliveryView() {
  const [profiles, setProfiles] = useState<ProfileMeta[]>(FALLBACK_PROFILES);
  const [profileId, setProfileId] = useState<string>(
    FALLBACK_PROFILES[0].profile_id,
  );
  const [packageType, setPackageType] = useState<PackageType>('hero');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    getMetaStates()
      .then((meta) => {
        if (active && meta.profiles.length > 0) setProfiles(meta.profiles);
      })
      .catch(() => {
        // Keep the truthful fallback; creation still works without /meta.
      });
    return () => {
      active = false;
    };
  }, []);

  const selected =
    profiles.find((p) => p.profile_id === profileId) ?? profiles[0];

  async function onStart() {
    setSubmitting(true);
    setError(null);
    try {
      const created = await createRun({
        profile_id: profileId,
        package_type: packageType,
      });
      navigateTo(`/runs/${created.run_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unexpected error.');
      setSubmitting(false);
    }
  }

  return (
    <>
      <form className="intake-grid" onSubmit={(e) => e.preventDefault()}>
        <section className="intake-card" aria-labelledby="package-input-heading">
          <div className="intake-card-index">01</div>
          <div className="intake-card-copy">
            <FileArchive aria-hidden="true" />
            <p className="eyebrow">Media package</p>
            <h2 id="package-input-heading">
              The finished master and its supporting files.
            </h2>
            <p>
              This demonstration inspects the frozen synthetic package for
              <em> The Last Lightkeeper</em>. Source assets are inspected in
              place and remain immutable; AIRCheck writes only to the delivery
              workspace.
            </p>
          </div>
          <label className="field-label" htmlFor="package-variant">
            Package variant
          </label>
          <Select
            value={packageType}
            onValueChange={(value) => {
              if (value === 'hero' || value === 'clean') setPackageType(value);
            }}
          >
            <SelectTrigger
              id="package-variant"
              className="air-select"
              aria-label="Package variant"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="hero">
                Hero master · injected delivery faults
              </SelectItem>
              <SelectItem value="clean">Clean master · no faults</SelectItem>
            </SelectContent>
          </Select>
          <div className="profile-receipt">
            <span className="profile-icon">
              <Package aria-hidden="true" />
            </span>
            <div>
              <strong>The Last Lightkeeper</strong>
              <p>
                {packageType === 'hero'
                  ? 'Delivery master with the canonical injected faults · fictional fixture v1'
                  : 'Clean delivery master · fictional fixture v1'}
              </p>
            </div>
          </div>
        </section>

        <section className="intake-card" aria-labelledby="spec-input-heading">
          <div className="intake-card-index">02</div>
          <div className="intake-card-copy">
            <FileCheck2 aria-hidden="true" />
            <p className="eyebrow">Destination specification</p>
            <h2 id="spec-input-heading">
              The requirements this package must satisfy.
            </h2>
            <p>
              Select a normalized Northstar destination profile. The same source
              package yields a different required QC plan per destination.
            </p>
          </div>
          <label className="field-label" htmlFor="destination-profile">
            Destination profile
          </label>
          <Select
            value={profileId}
            onValueChange={(value) => {
              if (typeof value === 'string') setProfileId(value);
            }}
          >
            <SelectTrigger
              id="destination-profile"
              className="air-select"
              aria-label="Destination profile"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {profiles.map((p) => (
                <SelectItem key={p.profile_id} value={p.profile_id}>
                  {p.profile_name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <div className="profile-receipt">
            <span className="profile-icon">
              <Shield aria-hidden="true" />
            </span>
            <div>
              <strong>{selected.profile_name}</strong>
              <p>{selected.description}</p>
            </div>
          </div>
        </section>
      </form>

      <div className="intake-action-bar">
        <div>
          <p className="eyebrow">Readiness</p>
          <p className="action-note">
            {submitting
              ? 'AIRCheck is inspecting the package and executing the deterministic QC plan…'
              : 'Start a real deterministic run over the synthetic package.'}
          </p>
          {error ? (
            <p className="decision-error" role="alert">
              {error}
            </p>
          ) : null}
        </div>
        <Button
          type="button"
          className="button-primary"
          disabled={submitting}
          onClick={onStart}
        >
          {submitting ? 'Starting…' : 'Start delivery check'}
        </Button>
      </div>
    </>
  );
}
